"""Verify S3 access from inside Docker (EC2 IAM role or explicit keys)."""
from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Test S3 bucket access and print the active AWS caller identity."

    def handle(self, *args, **options):
        bucket = (getattr(settings, "AWS_STORAGE_BUCKET_NAME", "") or "").strip()
        region = (getattr(settings, "AWS_S3_REGION_NAME", "") or "").strip()

        if not bucket:
            self.stderr.write(
                self.style.ERROR(
                    "AWS_STORAGE_BUCKET_NAME is not set — S3 media storage is disabled."
                )
            )
            return

        self.stdout.write(f"Bucket: {bucket}")
        self.stdout.write(f"Region: {region or '(default)'}")
        self.stdout.write(
            f"Using explicit keys: {bool(getattr(settings, 'AWS_ACCESS_KEY_ID', ''))}"
        )

        try:
            import boto3
        except ImportError as exc:
            self.stderr.write(self.style.ERROR(f"boto3 not installed: {exc}"))
            return

        s3 = boto3.client("s3", region_name=region or None)

        try:
            identity = boto3.client("sts", region_name=region or None).get_caller_identity()
            self.stdout.write(self.style.SUCCESS("STS caller identity:"))
            for key in ("Arn", "Account", "UserId"):
                self.stdout.write(f"  {key}: {identity.get(key)}")
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f"STS get_caller_identity failed: {exc}"))
            return

        try:
            response = s3.list_objects_v2(Bucket=bucket, MaxKeys=5)
            count = response.get("KeyCount", 0)
            self.stdout.write(self.style.SUCCESS(f"list_objects_v2 OK — KeyCount={count}"))
            for obj in response.get("Contents") or []:
                self.stdout.write(f"  - {obj.get('Key')}")
        except Exception as exc:
            self.stderr.write(self.style.ERROR(f"S3 list_objects_v2 failed: {exc}"))
