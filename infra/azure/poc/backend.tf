// Isolated local state avoids provisioning another storage account for this PoC.
terraform {
  backend "local" {}
}
