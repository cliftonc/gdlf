"""One-time enterprise signup flow.

Called from `./gdlf amapi enterprise signup` and `./gdlf amapi enterprise
complete`. After completion, `client.load_enterprise()` works and the rest
of the AMAPI surface can mint tokens + push policy.
"""
from __future__ import annotations

from . import client


def create_signup_url(callback_url: str) -> dict:
    """Create an EMM signup URL the parent visits in their browser.

    Google redirects to `callback_url` with `?enterpriseToken=...` after the
    parent agrees. The callback can be any URL — we only need the query
    string parameter, which the parent pastes back into the CLI.

    Returns the raw API response: `{name, url}`.
    """
    svc = client.service()
    return (
        svc.signupUrls()
        .create(projectId=client.project_id(), callbackUrl=callback_url)
        .execute()
    )


def complete_signup(*, enterprise_token: str, signup_url_name: str,
                    display_name: str = "gdlf") -> client.EnterpriseConfig:
    """Finalise the signup with the token returned by Google's redirect.

    Writes enterprise.json + caches the EnterpriseConfig for the process.
    """
    svc = client.service()
    body = {"enterpriseDisplayName": display_name}
    resp = (
        svc.enterprises()
        .create(
            projectId=client.project_id(),
            signupUrlName=signup_url_name,
            enterpriseToken=enterprise_token,
            body=body,
        )
        .execute()
    )
    cfg = client.EnterpriseConfig(
        name=resp["name"],
        project_id=client.project_id(),
        signup_url_name=signup_url_name,
        display_name=resp.get("enterpriseDisplayName") or display_name,
    )
    client.save_enterprise(cfg)
    return cfg
