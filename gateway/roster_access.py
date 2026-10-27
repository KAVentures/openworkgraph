"""Current roster checks for verified identities; no inferred email association."""


def active_identity(db, organization_id, *identities):
    """Return one exact active roster match, or None for an unregistered identity.

    Only verified configured actor/subject values are supplied. The roster is
    optional, but a known inactive or conflicting identity cannot fall through
    to group-scoped access. Two rows suffice to detect an ambiguous mapping.
    """
    values=sorted({str(value).strip() for value in identities if value})
    if not values:return None
    terms=[];params=[organization_id]
    for value in values:
        terms.append('(e.sso_subject=? OR e.actor_id=? OR e.email=?)')
        params.extend([value,value,value.lower()])
    with db.connect() as conn:
        rows=db._execute(conn,'SELECT e.actor_id,e.status,e.employee_id,COALESCE(a.generation,0) FROM gateway_employees e LEFT JOIN gateway_employee_access_epochs a ON a.employee_id=e.employee_id WHERE e.organization_id=? AND ('+' OR '.join(terms)+') LIMIT 2',params).fetchall()
    if len(rows)>1:raise ValueError('Verified identity has conflicting roster mappings')
    if not rows:return None
    if rows[0][1]!='active':raise ValueError('Roster identity is inactive')
    return {'actor_id':rows[0][0],'binding':{'employee_id':rows[0][2],'generation':int(rows[0][3])}}
