import base64

from django.core.management.base import BaseCommand

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b'=').decode('ascii')


class Command(BaseCommand):
    help = 'Generate VAPID keys for Web Push.'

    def handle(self, *args, **options):
        private_key = ec.generate_private_key(ec.SECP256R1())
        public_key = private_key.public_key()

        public_bytes = public_key.public_bytes(
            encoding=serialization.Encoding.X962,
            format=serialization.PublicFormat.UncompressedPoint,
        )
        private_number = private_key.private_numbers().private_value
        private_bytes = private_number.to_bytes(32, byteorder='big')

        self.stdout.write('WEBPUSH_VAPID_PUBLIC_KEY=' + _b64url(public_bytes))
        self.stdout.write('WEBPUSH_VAPID_PRIVATE_KEY=' + _b64url(private_bytes))
        self.stdout.write('WEBPUSH_VAPID_CLAIMS_SUB=mailto:admin@example.com')
