"""测试包。

加 __init__.py 是为了让测试模块可以用相对导入引用 conftest 里的假数据；
配合 pytest.ini 的 `pythonpath = .`，`app` 包也能正常导入。
"""
