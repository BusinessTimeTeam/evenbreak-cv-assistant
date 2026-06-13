# Remote state in a dedicated DigitalOcean Spaces bucket (S3-compatible).
#
# The bucket (evenbreak-cv-assistant-tfstate, tor1, versioning enabled) is
# created and managed OUT OF BAND — deliberately not a Terraform resource — so
# that `terraform destroy` of this stack can never delete the bucket holding its
# own state.
#
# Credentials come from AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY (set to the
# Spaces key by env.sh), not from the provider's spaces_* vars.
terraform {
  backend "s3" {
    bucket = "evenbreak-cv-assistant-tfstate"
    key    = "deploy/terraform.tfstate"
    region = "us-east-1" # dummy; required by the s3 backend, ignored by Spaces

    endpoints = {
      s3 = "https://tor1.digitaloceanspaces.com"
    }

    # DigitalOcean Spaces is S3-compatible but not AWS, so skip AWS-specific
    # validation and the checksum behaviour Spaces does not support.
    skip_credentials_validation = true
    skip_metadata_api_check     = true
    skip_region_validation      = true
    skip_requesting_account_id  = true
    skip_s3_checksum            = true
    use_path_style              = true
  }
}
