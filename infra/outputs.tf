output "name_servers" {
  description = "Enter these as NS records for the subdomain at your DNS registrar."
  value       = aws_route53_zone.site.name_servers
}

output "public_ip" {
  value = aws_eip.app.public_ip
}

output "instance_id" {
  description = "For SSM sessions and the deploy workflow (#23)."
  value       = aws_instance.app.id
}

output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}

output "github_deploy_role_arn" {
  description = "The role the deploy workflow (#23) assumes through OIDC."
  value       = aws_iam_role.github_deploy.arn
}

output "data_volume_id" {
  description = "The job database's volume (#24), for the snapshot restore in the runbook."
  value       = aws_ebs_volume.data.id
}

output "cognito_user_pool_id" {
  description = "For creating accounts from the CLI (#25)."
  value       = aws_cognito_user_pool.users.id
}
