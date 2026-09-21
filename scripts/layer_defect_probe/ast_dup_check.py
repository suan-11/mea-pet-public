"""Re-runnable check behind the WP-G record's "8 条语句整段重复" claim.

Rule (must be stated with the numbers, agents-rules §16.3): count top-level
statements of `_init_layer_overlay_mode` as `ast.dump` strings, DROPPING any
bare string expression (`ast.Expr(ast.Constant(str))`) -- that function carries
a non-docstring bare string near render_host.py:663, so a "docstring-only"
rule reads 24/16 while this rule reads 23/15.
"""
import ast, collections, subprocess

FN = '_init_layer_overlay_mode'


def stmts(src):
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.FunctionDef) and node.name == FN:
            return [ast.dump(s) for s in node.body
                    if not (isinstance(s, ast.Expr)
                            and isinstance(s.value, ast.Constant)
                            and isinstance(s.value.value, str))]
    raise SystemExit(f'{FN} not found')


old = stmts(subprocess.run(['git', 'show', f'HEAD:meapet/desktop/render_host.py'],
                           capture_output=True, text=True).stdout)
new = stmts(open('meapet/desktop/render_host.py').read())
co, cn = collections.Counter(old), collections.Counter(new)
removed = {k: co[k] - cn[k] for k in co if co[k] > cn[k]}
new_only = sum(cn[k] - co[k] for k in cn if cn[k] > co[k])
print(f'old={len(old)} new={len(new)} removed={sum(removed.values())} new_only={new_only}')
print('every removed statement still survives:', all(cn[k] >= 1 for k in removed))
