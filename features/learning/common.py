from flask import Blueprint

import bridge

bp = Blueprint("learning", __name__)


def C():
    return bridge.C
