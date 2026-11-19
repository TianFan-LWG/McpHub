"""McpHub stdio 启动器：绝对路径调用，避免 WorkBuddy 对 cwd 字段的规范化判定。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp_hub.server import main

main()
