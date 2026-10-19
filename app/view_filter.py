"""Explicit per-view scope; page purpose is decided by the stage-one agent."""
def role(view):
    if view.get('kind')=='ISOMETRIC':
        return 'EXCLUDE' if view.get('takeoff_role')=='EXCLUDE' else 'REFERENCE'
    return view.get('takeoff_role','MEMBERS')



def eligible(view):
    return role(view)=='MEMBERS'
