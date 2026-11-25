"""The web app's sign-in, closed sign-up, invitations and security headers."""

import re

import pytest
from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import CommandError, call_command
from django.urls import reverse

pytestmark = pytest.mark.django_db


def _invite(email="owner@example.com", staff=False):
    args = [email] + (["--staff"] if staff else [])
    call_command("invite", *args)
    return get_user_model().objects.get(email=email)


def test_health_answers_without_sign_in(client):
    response = client.get("/api/healthz")
    assert response.status_code == 200
    assert response.content == b"ok"


def test_login_lives_at_slash_login(client):
    assert reverse("account_login") == "/login"
    response = client.get("/login")
    assert response.status_code == 200
    assert b'name="login"' in response.content


def test_cabinet_needs_sign_in(client):
    response = client.get("/app")
    assert response.status_code == 302
    assert response["Location"].startswith("/login?next=/app")


def test_admin_uses_the_site_sign_in(client):
    response = client.get("/app/admin/", follow=True)
    assert response.request["PATH_INFO"] == "/login"


def test_public_sign_up_is_closed(client):
    response = client.post(
        "/account/signup/", {"email": "x@example.com", "password1": "a-long-enough-password-1"}
    )
    assert not get_user_model().objects.filter(email="x@example.com").exists()
    assert response.status_code == 200


def test_invite_makes_a_verified_account_without_password():
    user = _invite()
    assert not user.has_usable_password()
    assert EmailAddress.objects.get(user=user).verified


def test_invite_refuses_a_second_account_for_one_address():
    _invite()
    with pytest.raises(CommandError):
        call_command("invite", "OWNER@example.com")


def test_invite_refuses_what_is_not_an_address():
    with pytest.raises(CommandError):
        call_command("invite", "not-an-address")


def test_invited_owner_signs_in_with_an_emailed_code(client):
    _invite()
    response = client.post(reverse("account_request_login_code"), {"email": "owner@example.com"})
    assert response.status_code == 302
    assert len(mail.outbox) == 1
    code = re.search(r"^([A-Z0-9]{4}-[A-Z0-9]{4})$", mail.outbox[0].body, re.MULTILINE).group(1)
    response = client.post(reverse("account_confirm_login_code"), {"code": code})
    assert response.status_code == 302
    assert client.get("/app").status_code == 200


def test_password_sign_in_and_minimum_length(client):
    user = _invite()
    user.set_password("correct horse battery staple")
    user.save()
    response = client.post(
        "/login", {"login": "owner@example.com", "password": "correct horse battery staple"}
    )
    assert response.status_code == 302
    response = client.post(
        reverse("account_change_password"),
        {
            "oldpassword": "correct horse battery staple",
            "password1": "short-pass1",
            "password2": "short-pass1",
        },
    )
    assert response.status_code == 200
    user.refresh_from_db()
    assert user.check_password("correct horse battery staple")


def test_cabinet_shows_the_account_and_is_not_cached(client):
    user = _invite()
    client.force_login(user)
    response = client.get("/app")
    assert b"owner@example.com" in response.content
    assert response["Cache-Control"] == "no-store"


def test_security_headers(client):
    response = client.get("/login")
    csp = response["Content-Security-Policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert response["X-Robots-Tag"] == "noindex, nofollow"
    assert response["X-Frame-Options"] == "DENY"
    assert response["Referrer-Policy"] == "same-origin"


def test_spanish_pages(client):
    response = client.get("/login", HTTP_ACCEPT_LANGUAGE="es")
    assert b'lang="es"' in response.content


def test_spanish_cabinet(client):
    client.force_login(_invite())
    response = client.get("/app", HTTP_ACCEPT_LANGUAGE="es")
    assert "Cerrar sesión" in response.content.decode()
