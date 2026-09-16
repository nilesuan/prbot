# The resource shape from infrastructure-core commit 9b67058c, the merged
# version, after the comparison review's SEC-IAC-01 finding was fixed.
#
# The configuration block mirrors the live analyzer, captured by the change
# author via:
#   aws accessanalyzer get-analyzer --analyzer-name UnusedAccess \
#     --region ap-southeast-2
# in management account 215457784173 on 2026-09-16:
#   configuration.unusedAccessAge: 90
#   configuration.analysisRule.exclusions: [{ accountIds: ["631965728858"] }]
#
# lifecycle.prevent_destroy is omitted here on purpose. It is the SEC-IAC-02
# fix and it is correct, but including it would abort the plan with a
# prevent_destroy error rather than showing the diff, which is what this
# experiment is measuring.

terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "5.100.0"
    }
  }
}

provider "aws" {
  region = "ap-southeast-2"

  access_key                  = "test"
  secret_key                  = "test"
  skip_credentials_validation = true
  skip_requesting_account_id  = true
  skip_metadata_api_check     = true
  skip_region_validation      = true
}

resource "aws_accessanalyzer_analyzer" "unused_access_apse2" {
  analyzer_name = "UnusedAccess"
  type          = "ORGANIZATION_UNUSED_ACCESS"

  configuration {
    unused_access {
      unused_access_age = 90

      analysis_rule {
        exclusion {
          account_ids = ["631965728858"]
        }
      }
    }
  }

  tags = {
    Component = "AccessAnalyzer"
  }
}
