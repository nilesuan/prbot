# The resource shape from infrastructure-core commit eaa3480b, which prbot
# reviewed and returned verdict=APPROVE score=100 findings=0 hidden=0.
#
# Reproduced verbatim from:
#   git show eaa3480b:layers/management/main.tf | sed -n '2650,2660p'
#
# The count and provider alias are dropped because they are irrelevant to the
# defect: the defect is the absence of a `configuration` block on a resource
# being adopted by an `import` block. Every attribute on
# aws_accessanalyzer_analyzer is ForceNew, so an attribute present in state and
# absent from config forces replacement.

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

  # No AWS call is made: the plan runs with -refresh=false and the resource is
  # already in state. These skips stop the provider from trying to validate
  # credentials at configure time.
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

  tags = {
    Component = "AccessAnalyzer"
  }
}
