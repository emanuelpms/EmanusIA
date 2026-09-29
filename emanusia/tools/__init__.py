def load_all():
    """Importa os módulos para que suas ferramentas se registrem."""
    from . import (files, web, excel, dashboard, powerbi, sap, workflow,  # noqa: F401
                   vision, desktop, connectors, mail, botops, access, data)
