# Reproduction: the destroy prbot approved

Proves that the resource shape prbot scored `100/100` would have destroyed and
recreated a live, organization-wide IAM Access Analyzer.

## Run it

```bash
./run.sh
```

Offline, reusing a provider binary an initialised OpenTofu layer already has:

```bash
PROVIDER_MIRROR=/path/to/layers/management/.terraform/providers ./run.sh
```

No AWS credentials are needed either way. `-refresh=false` means no API call is
made, and the resource is already present in the supplied state file.

## What it does

Two `tofu plan` runs against one state file, differing only in the resource
configuration:

| Config | Corresponds to | Expected |
|--------|----------------|----------|
| `original.tf` | infrastructure-core `eaa3480b`, which prbot reviewed as `verdict=APPROVE score=100 findings=0 hidden=0` | `must be replaced`, `Plan: 1 to add, 0 to change, 1 to destroy.` |
| `merged.tf` | infrastructure-core `9b67058c`, after the comparison review's `SEC-IAC-01` was fixed | `No changes.` |

`live-state.tfstate` represents the live analyzer as an `import` block would
populate it: `unused_access_age = 90` and
`analysis_rule.exclusion.account_ids = ["631965728858"]`. Those values were
captured by the change author from
`aws accessanalyzer get-analyzer --analyzer-name UnusedAccess --region ap-southeast-2`
in management account `215457784173` on 2026-09-16.

## Why the result holds

Every attribute on `aws_accessanalyzer_analyzer` is `ForceNew` in
`terraform-provider-aws`. An attribute that exists in state and is absent from
configuration is therefore not an in-place update; it is a replacement. Import
populates state from the live resource, so omitting `configuration` from the
HCL guarantees a destroy on the very next plan.

This is a property of import plus `ForceNew`, not a property of this resource
type or this document. Any adopted resource whose configuration declares fewer
`ForceNew` attributes than the live resource carries has the same failure.

## Recorded output

Run on 2026-09-16 with OpenTofu against `terraform-provider-aws` 5.100.0
(the version pinned in the consuming repository's
`layers/management/.terraform.lock.hcl`).

`original.tf`:

```
# aws_accessanalyzer_analyzer.unused_access_apse2 must be replaced
-/+ resource "aws_accessanalyzer_analyzer" "unused_access_apse2" {
      ~ arn           = "arn:aws:access-analyzer:ap-southeast-2:215457784173:analyzer/UnusedAccess" -> (known after apply)
      ~ id            = "UnusedAccess" -> (known after apply)
        tags          = {
            "Component" = "AccessAnalyzer"
        }
        # (3 unchanged attributes hidden)

      - configuration { # forces replacement
          - unused_access { # forces replacement
              - unused_access_age = 90 -> null # forces replacement

              - analysis_rule { # forces replacement
                  - exclusion { # forces replacement
                      - account_ids   = [ # forces replacement
                          - "631965728858",
                        ] -> null
                      - resource_tags = [] -> null
                    }
                }
            }
        }
    }

Plan: 1 to add, 0 to change, 1 to destroy.
```

`merged.tf`:

```
No changes. Your infrastructure matches the configuration.
```

## Note on `prevent_destroy`

The merged code also added `lifecycle { prevent_destroy = true }` to all four
analyzers, which was the `SEC-IAC-02` fix and is correct. It is deliberately
omitted from `merged.tf` here: with it present the plan aborts with a
`prevent_destroy` error instead of rendering a diff, which would hide the very
comparison this reproduction exists to show.
