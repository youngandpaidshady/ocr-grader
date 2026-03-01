from app import app, db, ClassModel

with app.app_context():
    classes_to_remove = ['ss1j', 'ss 1j', 'ss1q', 'ss 1q']
    
    # Query case-insensitively
    targets = ClassModel.query.filter(
        db.func.lower(ClassModel.name).in_(classes_to_remove)
    ).all()
    
    deleted_names = []
    
    for c in targets:
        deleted_names.append(c.name)
        db.session.delete(c)
        
    db.session.commit()
    print(f"Deleted classes (and cascading students): {deleted_names}")
    
