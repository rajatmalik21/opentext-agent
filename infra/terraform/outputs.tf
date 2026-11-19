output "resource_group" {
  value = azurerm_resource_group.rg.name
}

output "acr_login_server" {
  value = azurerm_container_registry.acr.login_server
}

output "acr_name" {
  value = azurerm_container_registry.acr.name
}

output "key_vault_name" {
  value = azurerm_key_vault.kv.name
}

output "app_fqdn" {
  description = "Internal FQDN unless external_ingress is true. Empty until deploy_app is true."
  value       = var.deploy_app ? azurerm_container_app.app[0].ingress[0].fqdn : ""
}
