#!/bin/bash
# Job script of the auto-restart group tickr-download (fetched by the group's wrapper as scripts/tickboot.sh). Owner: research thread.
# WHOLE tick history with the FROZEN converter, fed by the fleet runner tickrun.py (same folder in the repo) (claims: several machines share one day list; a machine that is taken away loses only its days in flight).
# Contract with the wrapper: resumes from S3 (a day is done by its manifest); on SIGTERM stops inside a minute; exit 0 ONLY when every day is done.
# Settings are objects under s3://<bucket>/jobs/download/ written by the research thread before the start:
#   raw        "on" = every raw day file is also stored under raw/ in the DEEP_ARCHIVE class with a record under rawman/; anything else = raw files are not kept
#   duckdb     the engine version the Mac converts with (same version here, or the machine refuses)
#   runner.md5 the md5 of the reviewed tickrun.py (a different file in the bucket: the machine refuses)
#   crosscheck written HERE: the result of the Mac-against-cloud comparison of one day. "PASS ..." lets the fleet work; "FAIL ..." stops the whole fleet (until := 0, desired := 0).
# Nothing here reads, prints or stores a credential: the Massive keys are read by the converter and the runner from the secret named in TICK_SECRET through the machine's role.
set -u
export HOME=/root PATH=/usr/local/bin:/root/.local/bin:$PATH AWS_DEFAULT_REGION=us-east-1
BK=tickr-research-407384449055; B=s3://$BK; J=$B/jobs/download; D=${TICK_DIR:-/data}; mkdir -p $D && cd $D
CONV_MD5=c1c1ead1751ba765f494af79235bddd7          # frozen converter (cand-wm2tick11c4d.py)
CMP_MD5=b4870fbe830c1fdc0e43d279ae638d11           # cmpday.py (the adopted comparison rule)
TYPES_MD5=fd1b9f017178a8edb6d2daf6234016fc         # types-5y.csv, the Mac's list
XDAY=2026-11-16                                    # the day compared with the Mac's files
TP=/Users/valentynd/Development/Trading/.scratch/massive-tools/research/types-5y.csv   # the SAME path string as on the Mac: the converter's skip test compares option TICK_TYPES literally
IID=$(T=$(curl -s -m 2 -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 60'); curl -s -m 2 -H "X-aws-ec2-metadata-token: $T" http://169.254.169.254/latest/meta-data/instance-id); IID=${IID:-unknown-$$}
LOG=$D/boot-$IID.log; say() { echo "$(date -u +%FT%TZ) $IID $*" >> $LOG; aws s3 cp $LOG $J/logs/boot-$IID.log --only-show-errors; }
stop_fleet() { say "FLEET STOP: $*"; echo 0 | aws s3 cp - $J/until --only-show-errors; aws autoscaling set-desired-capacity --auto-scaling-group-name tickr-download --desired-capacity 0 || true; }
trap ':' TERM   # the runner gets the same signal (process group) and ends by itself; this shell only waits for it

DV=$(aws s3 cp $J/duckdb - 2>/dev/null | tr -dc '0-9.'); [ -n "$DV" ] || { say "REFUSED: no engine version in jobs/download/duckdb"; exit 1; }
RUN_MD5_WANT=$(aws s3 cp $J/runner.md5 - 2>/dev/null | tr -dc '0-9a-f' | head -c 32); [ ${#RUN_MD5_WANT} -eq 32 ] || { say "REFUSED: no runner md5 in jobs/download/runner.md5"; exit 1; }
[ -x $D/v/bin/python ] || { uv venv $D/v --python 3.12 && uv pip install --python $D/v/bin/python boto3 "duckdb==$DV"; } >/dev/null 2>&1 || { say "REFUSED: python environment failed"; exit 1; }
PY=$D/v/bin/python
[ "$($PY -c 'import duckdb; print(duckdb.__version__)')" = "$DV" ] || { say "REFUSED: the engine version is not $DV"; exit 1; }
aws s3 cp $B/scripts/mac-split/cand-wm2tick11c4d.py $D/wm2tick-v11.py --only-show-errors && aws s3 cp $B/scripts/fleet/tickrun.py $D/tickrun.py --only-show-errors && aws s3 cp $B/scripts/ramp/cmpday.py $D/cmpday.py --only-show-errors || { say "REFUSED: a script could not be fetched"; exit 1; }
mkdir -p "$(dirname $TP)"; aws s3 cp $B/reference/types-5y.csv $TP --only-show-errors || { say "REFUSED: type list could not be fetched"; exit 1; }
m() { md5sum "$1" | cut -c1-32; }
say "type $(curl -s -m 2 -H "X-aws-ec2-metadata-token: $(curl -s -m 2 -X PUT http://169.254.169.254/latest/api/token -H 'X-aws-ec2-metadata-token-ttl-seconds: 60')" http://169.254.169.254/latest/meta-data/instance-type) cores $(nproc) mem $(free -g | awk 'NR==2{print $2}') GB disk free $(df -h $D | awk 'NR==2{print $4}') converter $(m $D/wm2tick-v11.py) runner $(m $D/tickrun.py) cmpday $(m $D/cmpday.py) types $(m $TP) duckdb $($PY -c 'import duckdb; print(duckdb.__version__)') python $($PY -V 2>&1)"
[ "$(m $D/wm2tick-v11.py)" = "$CONV_MD5" ] && [ "$(m $D/tickrun.py)" = "$RUN_MD5_WANT" ] && [ "$(m $D/cmpday.py)" = "$CMP_MD5" ] && [ "$(m $TP)" = "$TYPES_MD5" ] || { say "REFUSED: a file is not the frozen one"; exit 1; }
export TICK_SECRET=tickr-research/massive-flatfiles TICK_TYPES=$TP

# --- Mac against cloud, one day, once for the fleet. The machine that creates the claim object does it BEFORE its own runner; the others start working at once.
# A machine taken away during the comparison leaves the claim without a result: the research thread watches the first hour and removes the claim by hand (no automatic retake).
R=$(aws s3 cp $J/crosscheck - 2>/dev/null | head -c 4)
if [ "$R" = FAIL ]; then stop_fleet "an earlier comparison failed"; exit 1; fi
if [ "$R" != PASS ] && $PY - <<EOF
import boto3, botocore, sys
try: boto3.client('s3').put_object(Bucket='$BK', Key='jobs/download/crosscheck.claim', Body=b'$IID', IfNoneMatch='*')
except botocore.exceptions.ClientError: sys.exit(1)
EOF
then
  T0=$(date +%s); mkdir -p $D/wx $D/cmp/mac $D/cmp/aws; say "comparison of $XDAY with the Mac: started here"
  TICK_PX=$B/fleet-test/px TICK_THREADS=$(nproc) TICK_MEM=20GB $PY -W ignore $D/wm2tick-v11.py $XDAY $XDAY $D/wx $B/fleet-test/tk61 > $D/cross.out 2>&1; RC=$?
  say "comparison day converted rc=$RC in $(( $(date +%s) - T0 )) s: $(grep -h '^day \|^redo \|^skip \|FAILED\|REFUSED' $D/cross.out | tr '\n' ' ' | cut -c1-400)"
  OK=1
  for f in tk61:tk px:px s10:s10; do aws s3 cp $B/${f%%:*}/2026/$XDAY.parquet $D/cmp/mac/${f##*:}.parquet --only-show-errors && aws s3 cp $B/fleet-test/${f%%:*}/2026/$XDAY.parquet $D/cmp/aws/${f##*:}.parquet --only-show-errors || OK=0; done
  if [ $RC -eq 0 ] && [ $OK -eq 1 ] && $PY $D/cmpday.py $D/cmp/mac $D/cmp/aws > $D/cmp.out 2>&1; then
    cat $D/cmp.out >> $LOG; echo "PASS $IID $(date -u +%FT%TZ)" | aws s3 cp - $J/crosscheck --only-show-errors; say "comparison PASS"
  elif [ -e /work/STOP ]; then say "comparison interrupted by a stop"; aws s3 rm $J/crosscheck.claim --only-show-errors; exit 3
  else
    cat $D/cmp.out >> $LOG 2>/dev/null; echo "FAIL $IID $(date -u +%FT%TZ)" | aws s3 cp - $J/crosscheck --only-show-errors; stop_fleet "the cloud's files of $XDAY differ from the Mac's (or the day could not be made)"; exit 1
  fi
  rm -rf $D/wx $D/cmp
fi

# --- the run. One converter with every core; two days are fetched ahead of it. Oldest day first (the Mac goes newest first; whoever meets a finished day skips it).
RAWSET=$(aws s3 cp $J/raw - 2>/dev/null | tr -dc 'a-z' | head -c 3)
[ "$RAWSET" = on ] && export RUN_RAW=$B/raw
say "runner starts, raw archive: ${RUN_RAW:-off}"
TICK_PX=$B/px TICK_THREADS=$(nproc) TICK_MEM=20GB RUN_MD5=$CONV_MD5 RUN_FETCH=2 RUN_CONV=1 RUN_AHEAD=2 RUN_FLOOR_GB=40 RUN_CLAIM_MIN=20 RUN_SKIPTEST=1 STOP_FILE=/work/STOP RUN_LOG=$J/logs/tickrun-$IID.log \
  $PY -W ignore $D/tickrun.py $D/wm2tick-v11.py 2021-11-16 2026-11-19 0/1 $D/run $B/tk61 > $D/tickrun.out 2>&1
RC=$?
# A comparison that failed while this machine worked: its exit must not mark the job as finished.
[ "$(aws s3 cp $J/crosscheck - 2>/dev/null | head -c 4)" = FAIL ] && { say "runner ended rc=$RC, but the comparison failed: not finished"; exit 1; }
[ $RC -eq 0 ] && [ "$(aws s3 cp $J/crosscheck - 2>/dev/null | head -c 4)" != PASS ] && { say "runner ended rc=0, but there is no comparison result yet: not finished"; exit 1; }
say "runner ended rc=$RC: $(tail -2 $D/run/tickrun.log | tr '\n' ' ' | cut -c1-400)"
exit $RC
