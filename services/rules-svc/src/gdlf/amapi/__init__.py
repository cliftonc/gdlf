"""Android Management API (AMAPI) integration.

The Android moral equivalent of `gdlf.mdm` — but structurally much smaller
because Google hosts the DPC (`Android Device Policy`), handles push, and
acts as the device-to-cloud TLS terminator. We just call AMAPI from rules-svc
to declare policy + mint enrollment tokens + poll device state.

See `services/rules-svc/CLAUDE.md` § Android MDM for the operating model.
"""
