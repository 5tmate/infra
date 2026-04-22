# 5tmate Infrastructure

Pulumi (Python) stacks for `*.5tmate.threatreveal.org`. State is stored in a shared S3 bucket, namespaced by stack.

```
infra/
  dns/        Route53 hosted zone + NS delegation to threatreveal.org
  landing/    S3 + CloudFront static site hosting
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup.