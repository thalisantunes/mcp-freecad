"""Erros do mcp-freecad.

Códigos no formato MF-<área>-<n> para que a mensagem devolvida ao agente diga
onde olhar sem precisar de stack trace.
"""


class McpFreecadError(Exception):
    """Base. Todo erro exposto ao cliente MCP herda daqui."""

    code = "MF-GEN-0"

    def __str__(self) -> str:
        return f"[{self.code}] {super().__str__()}"


class FreecadNotFound(McpFreecadError):
    code = "MF-CMD-1"


class FreecadRunFailed(McpFreecadError):
    """O freecadcmd terminou sem gravar o resultado (crash, saída antes da hora)."""

    code = "MF-RUN-1"


class FreecadTimeout(McpFreecadError):
    code = "MF-RUN-2"


class OperationFailed(McpFreecadError):
    """O runner rodou e reportou falha na operação (arquivo inválido, script com erro...)."""

    code = "MF-OP-1"


class InvalidPath(McpFreecadError):
    code = "MF-PATH-1"


class InvalidArgument(McpFreecadError):
    code = "MF-ARG-1"
