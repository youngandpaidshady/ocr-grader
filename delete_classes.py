from app import app, db, ClassModel
import logging

logging.basicConfig(level=logging.INFO)

with app.app_context():
    classes = ClassModel.query.all()
    deleted_count = 0
    kept_count = 0
    for c in classes:
        # Case insensitive exact stripping
        if c.name.strip().upper().replace(" ", "") != "SS1Q":
            logging.info(f"Deleting Class: {c.name}")
            db.session.delete(c)
            deleted_count += 1
        else:
            logging.info(f"Keeping Class: {c.name}")
            kept_count += 1
            
    db.session.commit()
    logging.info(f"Finished. Deleted {deleted_count} classes. Kept {kept_count} classes.")
