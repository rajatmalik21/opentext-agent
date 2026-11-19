data "azurerm_client_config" "current" {}

resource "random_string" "suffix" {
  length  = 5
  upper   = false
  special = false
}

locals {
  name   = "${var.prefix}-${var.env}"
  flat   = "${var.prefix}${var.env}${random_string.suffix.result}"
  tags   = merge({ app = "ota-agent", env = var.env, managed_by = "terraform", data_class = "synthetic-poc" }, var.tags)
  image  = "${azurerm_container_registry.acr.login_server}/ota-agent:${var.image_tag}"
  model  = var.enable_claude ? "claude" : "extractive"
  secret = "anthropic-api-key"
}

resource "azurerm_resource_group" "rg" {
  name     = "rg-${local.name}"
  location = var.location
  tags     = local.tags
}

# One user-assigned identity: pulls images, reads the secret. No stored credentials anywhere.
resource "azurerm_user_assigned_identity" "app" {
  name                = "id-${local.name}"
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location
  tags                = local.tags
}

resource "azurerm_container_registry" "acr" {
  name                             = "acr${local.flat}"
  resource_group_name              = azurerm_resource_group.rg.name
  location                         = azurerm_resource_group.rg.location
  sku                              = "Basic"
  admin_enabled                    = false
  anonymous_pull_enabled           = false
  public_network_access_enabled    = true # PoC. Production: Premium + private endpoint (ADR-0007).
  tags                             = local.tags
}

resource "azurerm_role_assignment" "acr_pull" {
  scope                = azurerm_container_registry.acr.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

resource "azurerm_key_vault" "kv" {
  name                          = "kv${local.flat}"
  resource_group_name           = azurerm_resource_group.rg.name
  location                      = azurerm_resource_group.rg.location
  tenant_id                     = data.azurerm_client_config.current.tenant_id
  sku_name                      = "standard"
  rbac_authorization_enabled    = true
  purge_protection_enabled      = var.purge_protection
  soft_delete_retention_days    = 7

  network_acls {
    default_action = "Deny"
    bypass         = "AzureServices"
  }
}



resource "azurerm_role_assignment" "kv_secrets_user" {
  scope                = azurerm_key_vault.kv.id
  role_definition_name = "Key Vault Secrets User"
  principal_id         = azurerm_user_assigned_identity.app.principal_id
}

resource "azurerm_log_analytics_workspace" "law" {
  name                = "log-${local.name}"
  resource_group_name = azurerm_resource_group.rg.name
  location            = azurerm_resource_group.rg.location
  sku                 = "PerGB2018"
  retention_in_days   = var.log_retention_days
  tags                = local.tags
}

resource "azurerm_container_app_environment" "env" {
  name                       = "cae-${local.name}"
  resource_group_name        = azurerm_resource_group.rg.name
  location                   = azurerm_resource_group.rg.location
  log_analytics_workspace_id = azurerm_log_analytics_workspace.law.id
  tags                       = local.tags
}

resource "azurerm_container_app" "app" {
  count                        = var.deploy_app ? 1 : 0
  name                         = "ca-${local.name}"
  resource_group_name          = azurerm_resource_group.rg.name
  container_app_environment_id = azurerm_container_app_environment.env.id
  revision_mode                = "Single"
  tags                         = local.tags

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.app.id]
  }

  registry {
    server   = azurerm_container_registry.acr.login_server
    identity = azurerm_user_assigned_identity.app.id
  }

  dynamic "secret" {
    for_each = var.enable_claude ? [1] : []
    content {
      name                = local.secret
      key_vault_secret_id = var.anthropic_key_vault_secret_id
      identity            = azurerm_user_assigned_identity.app.id
    }
  }

  ingress {
    external_enabled           = var.external_ingress
    target_port                = 8080
    transport                  = "http"
    allow_insecure_connections = false
    traffic_weight {
      latest_revision = true
      percentage      = 100
    }
  }

  template {
    min_replicas = var.min_replicas
    max_replicas = var.max_replicas

    container {
      name   = "ota-agent"
      image  = local.image
      cpu    = 0.5
      memory = "1Gi"
      args   = ["serve", "--host", "0.0.0.0", "--port", "8080", "--model", local.model]

      env {
        name  = "OTA_LOG_LEVEL"
        value = "INFO"
      }
      env {
        name  = "OTA_RETRIEVAL_MODE"
        value = "bm25"
      }
      dynamic "env" {
        for_each = var.enable_claude ? [1] : []
        content {
          name        = "ANTHROPIC_API_KEY"
          secret_name = local.secret
        }
      }

      liveness_probe {
        transport = "HTTP"
        path      = "/healthz"
        port      = 8080
      }
      readiness_probe {
        transport = "HTTP"
        path      = "/readyz"
        port      = 8080
      }
    }
  }

  depends_on = [azurerm_role_assignment.acr_pull, azurerm_role_assignment.kv_secrets_user]
}

# Alerts and budget: only when someone is there to receive them.
resource "azurerm_monitor_action_group" "ops" {
  count               = var.alert_email == "" ? 0 : 1
  name                = "ag-${local.name}"
  resource_group_name = azurerm_resource_group.rg.name
  short_name          = "ota-ops"
  email_receiver {
    name          = "owner"
    email_address = var.alert_email
  }
  tags = local.tags
}

resource "azurerm_monitor_metric_alert" "restarts" {
  count               = var.alert_email != "" && var.deploy_app ? 1 : 0
  name                = "alert-restarts-${local.name}"
  resource_group_name = azurerm_resource_group.rg.name
  scopes              = [azurerm_container_app.app[0].id]
  description         = "Container restarts: crash loop or failing probe."
  severity            = 2
  frequency           = "PT5M"
  window_size         = "PT15M"
  criteria {
    metric_namespace = "Microsoft.App/containerapps"
    metric_name      = "RestartCount"
    aggregation      = "Total"
    operator         = "GreaterThan"
    threshold        = 3
  }
  action {
    action_group_id = azurerm_monitor_action_group.ops[0].id
  }
  tags = local.tags
}

resource "azurerm_consumption_budget_resource_group" "budget" {
  count             = var.alert_email == "" ? 0 : 1
  name              = "budget-${local.name}"
  resource_group_id = azurerm_resource_group.rg.id
  amount            = var.monthly_budget
  time_grain        = "Monthly"
  time_period {
    start_date = formatdate("YYYY-MM-01'T'00:00:00Z", timestamp())
  }
  notification {
    enabled        = true
    threshold      = 80
    operator       = "GreaterThan"
    contact_emails = [var.alert_email]
  }
  lifecycle {
    ignore_changes = [time_period]
  }
}
