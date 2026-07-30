import os

PACKAGES = ['agents', 'api', 'clients', 'config', 'llm', 'models', 'services', 'telemetry', 'utils']


def fix_imports(root, namespace):
    count = 0
    for dirpath, dirnames, filenames in os.walk(root):
        for fname in filenames:
            if not fname.endswith('.py'):
                continue
            fpath = os.path.join(dirpath, fname)
            with open(fpath, encoding='utf-8') as fh:
                lines = fh.readlines()
            new_lines = []
            changed = False
            for line in lines:
                stripped = line.lstrip()
                new_line = line
                for pkg in PACKAGES:
                    for prefix in ['from ' + pkg + '.', 'from ' + pkg + ' ']:
                        ns_prefix = 'from ' + namespace + '.' + pkg + prefix[len('from ' + pkg):]
                        if stripped.startswith(prefix) and not stripped.startswith('from ' + namespace):
                            new_line = line.replace(prefix, ns_prefix, 1)
                            changed = True
                            break
                new_lines.append(new_line)
            if changed:
                with open(fpath, 'w', encoding='utf-8') as fh:
                    fh.writelines(new_lines)
                count += 1
                print('fixed: ' + fpath)
    print(namespace + ': ' + str(count) + ' files fixed')


fix_imports('l2_rca', 'l2_rca')
fix_imports('db_fix', 'db_fix')
