"""Apple MDM implementation: enrollment profile generation, device identity
cert minting, check-in / command protocol handlers.

The high-level flow:

  1. Parent clicks "Enrol via MDM" in dashboard → POST /api/devices/{ip}/mdm/enroll-token
     → server mints a per-device identity cert (signed by the gdlf MDM CA),
     wraps it in a PKCS12, stashes a one-time enrolment token in SQLite.
  2. Parent loads the enrollment URL into Apple Configurator 2 (Mac, USB-cabled
     iPhone). Configurator wipes + supervises + fetches GET /mdm/enroll/{token}
     → server returns a signed .mobileconfig bundling the PKCS12 + MDM payload.
  3. Device installs the profile. Apple's MDM client now uses the PKCS12 cert
     for mTLS to /mdm/checkin (initial) and /mdm/server (subsequent commands).
  4. /mdm/checkin handles the Authenticate + TokenUpdate messages — we record
     UDID + APNs push token + push magic on the Device's MdmState.

See PARENT design doc: docs/design.md (MDM section to be added).
"""
