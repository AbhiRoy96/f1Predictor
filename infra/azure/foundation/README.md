---
title: Terraform Azure foundation
description: Private staging resources and separately protected foundation state.
---

## Usage

Follow the [staging preparation guide](../README.md#prepare-a-staging-foundation).
This root requires an existing resource group, bootstrap Key Vault, and Azure Blob
state container. Its state contains credentials; keep it inaccessible to daily releases.

Copy [terraform.tfvars.example](terraform.tfvars.example) and
[backend.hcl.example](backend.hcl.example) to ignored local files.
[variables.tf](variables.tf) defines required resource group, bootstrap vault,
budget recipients and budget start date inputs, plus optional prefix, budget,
log cap/retention and database backup retention settings.
[outputs.tf](outputs.tf) exports resource names and the Entra callback URI, never
secret values. Pass only the registry, vault and storage names to the app root.
