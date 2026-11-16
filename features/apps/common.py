"""Shared pieces: the blueprint and the link to the core app."""

from flask import Blueprint
import bridge


bp = Blueprint("apps", __name__)


def C():
    return bridge.C
