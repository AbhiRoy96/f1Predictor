---
title: Terraform Azure applications
description: Stateful application resources with ordered fail-closed releases.
---

## Usage

Follow the [build and release guide](../README.md#build-and-release).
This root requires the provisioned foundation, built images, Entra registration,
and its own Azure Blob state container. It uses resource metadata and Key Vault
secret references without reading secret values or foundation state.

Copy [terraform.tfvars.example](terraform.tfvars.example) and
[backend.hcl.example](backend.hcl.example) to ignored local files.
[variables.tf](variables.tf) defines the required foundation names, resource group,
image SHA, revision suffix and Entra IDs. Telemetry and schedules are optional.
The release script supplies release-specific values and resource group/prefix.
[outputs.tf](outputs.tf) exports the snapshot account name and manual init job name.

Initialize the backend, then use the release script rather than a direct apply.
The init stage targets only the database job; the full application plan follows
successful schema initialization. Web always starts internal and is exposed only
after the release script verifies Entra authentication.
