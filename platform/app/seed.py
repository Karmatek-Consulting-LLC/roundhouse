import logging

from sqlalchemy.orm import Session

from app.auth import hash_password
from app.config import ADMIN_EMAIL, ADMIN_PASSWORD, SERVERS_DATA_DIR
from app.db_models import ServerOwner, User

logger = logging.getLogger(__name__)


def seed_admin(db: Session) -> None:
    """Create the initial superadmin if none exists."""
    existing = db.query(User).filter(User.role == "superadmin").first()
    if existing:
        return

    if not ADMIN_EMAIL or not ADMIN_PASSWORD:
        logger.warning("No superadmin exists and ADMIN_EMAIL/ADMIN_PASSWORD not set")
        return

    admin = User(
        email=ADMIN_EMAIL,
        password_hash=hash_password(ADMIN_PASSWORD),
        display_name="Admin",
        role="superadmin",
    )
    db.add(admin)
    db.commit()
    db.refresh(admin)
    logger.info("Created superadmin: %s", ADMIN_EMAIL)

    # Assign existing servers to admin
    migrate_server_ownership(db, admin)


def migrate_server_ownership(db: Session, admin: User) -> None:
    """Assign unowned servers to the admin user."""
    if not SERVERS_DATA_DIR.exists():
        return

    for server_dir in SERVERS_DATA_DIR.iterdir():
        spec_path = server_dir / "server.json"
        if not spec_path.exists():
            continue

        server_name = server_dir.name
        existing = db.query(ServerOwner).filter(
            ServerOwner.server_name == server_name
        ).first()
        if existing:
            continue

        db.add(ServerOwner(server_name=server_name, owner_id=admin.id))
        logger.info("Assigned server '%s' to admin", server_name)

    db.commit()
