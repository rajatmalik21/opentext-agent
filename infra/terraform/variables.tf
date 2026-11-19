variable "subscription_id" {
  type        = string
  description = "Azure subscription to deploy into."
}

variable "location" {
  type        = string
  description = "Azure region. Choose for data residency; the Claude API call leaves Azure regardless (see ADR-0007)."
  default     = "australiaeast"
}

variable "env" {
  type        = string
  description = "Environment short name (dev|prod)."
  validation {
    condition     = contains(["dev", "prod"], var.env)
    error_message = "env must be dev or prod."
  }
}

variable "prefix" {
  type        = string
  description = "Short lowercase prefix for resource names."
  default     = "ota"
  validation {
    condition     = can(regex("^[a-z][a-z0-9]{1,8}$", var.prefix))
    error_message = "prefix must be 2-9 lowercase alphanumerics, starting with a letter."
  }
}

variable "deploy_app" {
  type        = bool
  description = "Create the Container App. Apply once with false, push an image to ACR, then apply with true."
  default     = false
}

variable "image_tag" {
  type        = string
  description = "Image tag in ACR (use the git SHA, never 'latest')."
  default     = "bootstrap"
}

variable "external_ingress" {
  type        = bool
  description = "Expose the app outside the Container Apps environment. Default false: the PoC server has no TLS termination of its own, no rate limiting and no per-user identity (README, ADR-0007)."
  default     = false
}

variable "enable_claude" {
  type        = bool
  description = "Run with --model claude. Requires anthropic_key_vault_secret_id. Default false: offline extractive model."
  default     = false
}

variable "anthropic_key_vault_secret_id" {
  type        = string
  description = "Versionless Key Vault secret URI holding the Anthropic API key. The VALUE is set out of band, never through Terraform (it would land in state)."
  default     = ""
}

variable "min_replicas" {
  type    = number
  default = 0
}

variable "max_replicas" {
  type    = number
  default = 2
}

variable "log_retention_days" {
  type    = number
  default = 30
}

variable "purge_protection" {
  type        = bool
  description = "Key Vault purge protection. true for prod; dev may set false so it can be torn down and recreated."
  default     = true
}

variable "alert_email" {
  type        = string
  description = "Recipient for the restart alert and budget notice. Empty disables both."
  default     = ""
}

variable "monthly_budget" {
  type        = number
  description = "Resource-group budget in the subscription currency. Used only when alert_email is set."
  default     = 100
}

variable "tags" {
  type    = map(string)
  default = {}
}
