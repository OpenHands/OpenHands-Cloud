TFVARS ?= terraform.tfvars

.PHONY: init plan apply destroy ssh ip fmt validate clean

init:
	terraform init

plan:
	terraform plan -var-file=$(TFVARS)

apply:
	terraform apply -var-file=$(TFVARS)

destroy:
	terraform destroy -var-file=$(TFVARS)

ssh:
	ssh -i $$(terraform output -raw ssh_key_file) ubuntu@$$(terraform output -raw instance_public_ip)

ip:
	@terraform output -raw instance_public_ip

fmt:
	terraform fmt -recursive

validate:
	terraform validate

clean:
	rm -rf .terraform .terraform.lock.hcl
	rm -f terraform.tfstate terraform.tfstate.backup
	rm -rf ssh/
