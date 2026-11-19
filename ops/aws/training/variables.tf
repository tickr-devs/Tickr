variable "region" {
  description = "AWS region for the training bucket and machine (us-east-1, next to the bot)."
  type        = string
  default     = "us-east-1"
}

variable "instance_type" {
  description = "Training machine type: CPU only, 32-64 vCPU and 128-256 GB RAM (training spec, stage 4)."
  type        = string
  default     = "c7a.16xlarge"
}

variable "disk_gb" {
  description = "Encrypted gp3 root disk of the training machine, in GB; deleted with the machine."
  type        = number
  default     = 500
}

variable "max_runtime_hours" {
  description = "Hard limit, in hours: the machine shuts itself down (and so terminates) this long after boot, whatever runs."
  type        = number
  default     = 24
}

variable "monthly_budget_usd" {
  description = "Monthly AWS budget for the whole account in USD: the bot (~80) plus the training sum the owner named."
  type        = number
  default     = 280
}

variable "budget_email" {
  description = "Address the budget alerts go to. Personal data: pass it at apply time (-var or an untracked tfvars file), never commit it."
  type        = string
}
