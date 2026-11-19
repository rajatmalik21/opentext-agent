# Terraform (UNVALIDATED)

Written without terraform, network or an Azure subscription. Nothing here has been planned or applied.
The CI `terraform` job (fmt, `init -backend=false`, `validate`) and a trivy config scan are the first real checks.

Creates: resource group, user-assigned managed identity, ACR (admin disabled), Key Vault (RBAC, purge protection by variable),
Log Analytics, Container Apps environment, the Container App, and (if `alert_email` set) a restart alert and budget.

Two-step first deploy (the app needs an image that does not exist yet):
```
terraform init -backend-config=backend.hcl
terraform apply -var-file=envs/dev.tfvars.example -var deploy_app=false
az acr build -r <acr_name> -t ota-agent:<sha> ../..
terraform apply -var-file=envs/dev.tfvars.example -var deploy_app=true -var image_tag=<sha>
```
For Claude: create the Key Vault secret yourself (`az keyvault secret set ...`; never through Terraform, it would be stored in state),
then set `enable_claude=true` and `anthropic_key_vault_secret_id`.

Known gaps: public ACR/Key Vault endpoints, no VNet, no WAF/auth in front, no remote-state bootstrap, azurerm 4.x attribute names not verified. See ADR-0007.
