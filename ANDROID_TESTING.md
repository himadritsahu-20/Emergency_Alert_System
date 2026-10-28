# Real-Device Alert Delivery

Relay Lab has two actual phone delivery channels:

- **Android Web Push** sends a system notification through the browser push service, including when the receiver tab is closed. The receiver must opt in once, and the server needs VAPID keys plus a trusted HTTPS URL reachable by the phone.
- **Twilio SMS** sends the translated scenario text to a saved phone number. SMS is independent of browser permissions and can incur provider/carrier charges.

The deterministic parser selects a reviewed scenario and authored language-table message. It does not machine-translate unknown free-form instructions. If no keyword matches, generic emergency wording is sent and identified as generic in the report.

## Android Web Push

1. Install dependencies into the project-local environment:

   ```sh
   uv venv .venv
   uv pip install --python .venv/bin/python -r requirements.txt
   ```

2. Generate and store VAPID keys. This writes the private key to the ignored `vapid_private.pem` file and appends settings to `.env`; it does not print key material:

   ```sh
   .venv/bin/python generate_vapid_keys.py --subject mailto:you@example.com
   ```

   Use a real contact address for `--subject`. Keep `.env` and `vapid_private.pem` private.

3. Serve Relay Lab on a trusted HTTPS hostname the Android device can reach. Browser push does not work from a plain HTTP LAN address. Configure a TLS-terminating reverse proxy or provide a trusted certificate and key:

   ```dotenv
   HOST=0.0.0.0
   PORT=8443
   SSL_CERTFILE=/path/to/trusted-cert.pem
   SSL_KEYFILE=/path/to/private-key.pem
   ```

   Start the app with `.venv/bin/python webapp.py`, then open its HTTPS URL on the phone. The development server has no user authentication; keep it behind a trusted private network or authenticated proxy and do not expose it directly to the public internet.

4. From a recipient row, open its **Android** link on the phone and press **Enable push notifications**. Grant permission. The device subscription is saved in the ignored `data/push_subscriptions.json` file and survives server restarts.
5. Broadcast an alert. Push is sent to registered recipients only after simulated delivery is acknowledged. Each row reports push status. Opening the phone notification shows the receiver page with large translated text and pictograms.

Android settings can revoke permission or battery-restrict background delivery. `sent` means the push service accepted the request; it is not a handset read receipt.

## SMS

1. Add Twilio settings to `.env`:

   ```dotenv
   TWILIO_ACCOUNT_SID=your_account_sid
   TWILIO_AUTH_TOKEN=your_auth_token
   TWILIO_FROM_NUMBER=+15551234567
   ```

2. Select a recipient, enter its phone number in E.164 format (for example, `+919876543210`), and save it.
3. Check **Send real SMS to configured devices** before broadcasting. SMS is attempted only for acknowledged deliveries with a saved number.
4. Check per-recipient SMS status. `accepted by gateway` is not a handset delivery receipt. Twilio trial accounts may restrict recipients.
