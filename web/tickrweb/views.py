"""Pages of the web app that are not allauth's."""

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_GET


@require_GET
def healthz(request):
    """For Caddy and the uptime check: the process answers. Says nothing else."""
    return HttpResponse("ok", content_type="text/plain")


@login_required
@require_GET
def cabinet(request):
    return render(request, "cabinet/index.html")
