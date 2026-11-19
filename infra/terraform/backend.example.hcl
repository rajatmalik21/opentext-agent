# Copy to backend.hcl (git-ignored) and fill in. State holds sensitive data: use a private
# storage account, RBAC-only access, versioning and soft delete on the container.
resource_group_name  = "<state-rg>"
storage_account_name = "<state-storage-account>"
container_name       = "tfstate"
key                  = "ota-agent.dev.tfstate"
use_azuread_auth     = true
