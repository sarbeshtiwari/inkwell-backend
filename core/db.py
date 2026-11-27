"""MongoDB connection (`M`): lazily connected collection handles and the GridFS bucket."""

from gridfs import GridFSBucket
from pymongo import MongoClient

from core.config import MONGODB_DB, MONGODB_URI


def normalised(uri):
    """Some copied connection strings separate options with ';' as well as '&'; MongoClient accepts only one kind."""
    base, sep, opts = uri.partition("?")
    return base + sep + opts.replace(";", "&") if sep else uri


class Mongo:
    """Lazily connected handles to the MongoDB collections and GridFS bucket."""

    def __init__(self):
        self.client = self.db = self.files = None

    def connect(self):
        self.client = MongoClient(normalised(MONGODB_URI), serverSelectionTimeoutMS=8000, appname="reachout", tz_aware=True)
        self.client.admin.command("ping")
        self.db = self.client[MONGODB_DB]
        self.files = GridFSBucket(self.db, bucket_name="files")

    def __getattr__(self, name):  # M.users, M.kv, M.send_log, ...
        if self.db is None:
            raise RuntimeError("Database not connected")
        return self.db[name]


M = Mongo()


def ensure_index(coll, keys, **options):
    """Create an index only if the collection doesn't have one with that name already.

    create_index on an existing index is a no-op for the data, but the server still takes locks to check it; with
    many indexes and several processes starting at once, those checks slow down everything else. Listing the
    indexes (one read per collection) avoids them."""
    if isinstance(keys, str):
        keys = [(keys, 1)]
    name = options.get("name") or "_".join(f"{k}_{d}" for k, d in keys)
    try:
        if name in coll.index_information():
            return name
    except Exception:  # noqa: BLE001 - a collection that doesn't exist yet has no indexes: create below
        pass
    return coll.create_index(keys, **options)
