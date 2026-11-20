#!/bin/bash
# First boot of a training machine. Hard stop: the machine powers off (and so terminates,
# launch template) ${max_runtime_minutes} minutes after boot, whatever is running. A machine of
# an auto-restart group is then replaced and its job resumes from S3.
set -eu
shutdown -h +${max_runtime_minutes}
apt-get update -q
apt-get install -y -q python3-venv git unzip curl
curl -sSfL https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip -o /tmp/awscli.zip
unzip -q /tmp/awscli.zip -d /tmp && /tmp/aws/install
curl -LsSf https://astral.sh/uv/install.sh | UV_INSTALL_DIR=/usr/local/bin sh
install -d -o ubuntu -g ubuntu /work
echo "bucket=${bucket}" > /work/README
%{ if job != "" ~}

# --- Auto-restart job "${job}" (group ${group}) -----------------------------------------------
# Contract for the job script (s3://${bucket}/${job_script}, owned by research):
# - resume from what it already saved in S3; save progress to S3 as often as it can;
# - on SIGTERM, or when /work/STOP appears, save and exit within 90 seconds;
# - exit 0 only when the whole job is finished.
# The wrapper runs it only while s3://${bucket}/jobs/${job}/until holds a future Unix time
# (written after the owner's yes on a named sum) and no jobs/${job}/done exists.
cat > /usr/local/bin/tickr-job <<'WRAP'
#!/bin/bash
set -u
export AWS_DEFAULT_REGION=${region} BUCKET=${bucket} JOB=${job} STOP_FILE=/work/STOP
M=s3://${bucket}/jobs/${job}
stop_group() {
  aws autoscaling set-desired-capacity --auto-scaling-group-name ${group} --desired-capacity 0 || true
  shutdown -h now
}
allowed() {
  u=$(aws s3 cp "$M/until" - 2>/dev/null | tr -dc 0-9)
  [ -n "$u" ] && [ "$(date +%s)" -lt "$u" ] && ! aws s3 ls "$M/done" >/dev/null 2>&1
}
spot_notice() {
  t=$(curl -s -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 60')
  curl -sf -o /dev/null -H "X-aws-ec2-metadata-token: $t" http://169.254.169.254/latest/meta-data/spot/instance-action
}
allowed || { echo "not allowed: no future deadline or job done"; stop_group; exit 0; }
aws s3 cp "s3://${bucket}/${job_script}" /work/job.sh || { sleep 60; shutdown -h now; exit 1; }
cd /work
setsid bash /work/job.sh &
pid=$!
n=0
while kill -0 "$pid" 2>/dev/null; do
  sleep 5
  n=$((n + 1))
  if spot_notice; then
    echo "spot interruption notice: stopping the job to save"
    touch /work/STOP; kill -TERM -- "-$pid" 2>/dev/null; wait "$pid"; exit 0
  fi
  if [ $((n % 12)) -eq 0 ] && ! allowed; then
    echo "deadline passed or done marker set: stopping"
    touch /work/STOP; kill -TERM -- "-$pid" 2>/dev/null; wait "$pid"; stop_group; exit 0
  fi
done
wait "$pid"; rc=$?
if [ "$rc" -eq 0 ]; then
  date -u +%s | aws s3 cp - "$M/done"
  echo "job finished"; stop_group
else
  date -u +%s | aws s3 cp - "$M/last-failure"
  echo "job exited $rc: machine restarts, the group brings a fresh one"; sleep 60; shutdown -h now
fi
WRAP
chmod 755 /usr/local/bin/tickr-job
nohup /usr/local/bin/tickr-job >> /var/log/tickr-job.log 2>&1 &
%{ endif ~}
