# The zone for the subdomain only. The owner delegates it by entering the
# name_servers output as NS records for the subdomain at their DNS registrar.
resource "aws_route53_zone" "site" {
  name          = var.domain
  force_destroy = true
}

resource "aws_route53_record" "site" {
  zone_id = aws_route53_zone.site.zone_id
  name    = var.domain
  type    = "A"
  ttl     = 300
  records = [aws_eip.app.public_ip]
}
