# Production Postal SMTP module — separate Compose project

Postal is **not installed** by the greenfield starter. At implementation milestone M4, install it using the official `postalserver/install` bootstrap helper and official Compose file, pinned to a reviewed release. Keep its own persistence, host, domain, SSL and deployment lifecycle separate from the SubSphere app. Connect the SubSphere notification worker through configured SMTP credentials. The platform's mail sender interface must switch between Mailpit (dev) and Postal (production) without changing business rules.

References: https://docs.postalserver.io/getting-started/prerequisites/ and https://docs.postalserver.io/getting-started/postal-command/

Postal recommends a dedicated capable host and real outbound SMTP connectivity (port 25 may be blocked). Configure SPF/DKIM/DMARC and bounce/delivery webhooks; success at SMTP handoff is **not** guaranteed inbox delivery.
