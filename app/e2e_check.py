# -*- coding: utf-8 -*-
"""端到端检查：真实文件改名 / 撤回 / 冲突拦截 / 目录逐层浏览

    E:\\Python312\\python.exe app\\e2e_check.py

全程操作真实磁盘，任何断言失败都会抛 AssertionError。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bridge as bridge_mod  # noqa: E402
import rules  # noqa: E402

PASSED: list[str] = []


def check(title: str, condition: bool, detail: str = '') -> None:
    if not condition:
        raise AssertionError('%s 失败 %s' % (title, detail))
    PASSED.append(title)
    print('  [ok] %s%s' % (title, ('  %s' % detail) if detail else ''))


def step(text: str) -> None:
    print('\n== %s ==' % text)


def make_app(workflow=None) -> bridge_mod.Bridge:
    app = bridge_mod.Bridge({})
    if workflow:
        app.workflow = workflow
    return app


def names(app) -> list[str]:
    return [i.name for i in app.items]


def previews(app) -> list[str]:
    return [i.new_name for i in app.items]


def main() -> None:
    root = tempfile.mkdtemp(prefix='fr_e2e_')
    sub = os.path.join(root, 'sub dir')
    os.makedirs(sub)
    for path, content in {
        os.path.join(root, 'Vacation Photo 01.JPG'): 'aaa',
        os.path.join(root, 'photo 2.jpg'): 'bbb',
        os.path.join(sub, 'notes draft.TXT'): 'ccc',
    }.items():
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(content)

    try:
        # ---------------------------------------------------------- 导入不展开
        step('导入文件夹只保留目录节点')
        app = make_app([
            {'id': 'clean', 'enabled': True,
             'config': dict(rules.default_config('clean'), mode='underscore')},
            {'id': 'case', 'enabled': True, 'config': {'mode': 'lower', 'onlyBase': False}},
        ])
        app.add_paths([root])
        check('顶层只有导入的那个文件夹', names(app) == [os.path.basename(root)], str(names(app)))
        check('面包屑为空（还在导入层）', app.crumbs == [])

        # ---------------------------------------------------------- 进入目录
        step('点击文件夹进入，只看直接子项')
        payload = app.enter_dir(app.items[0].tag)
        check('进入后列出直接子项，文件夹在前',
              names(app)[0] == 'sub dir' and
              sorted(names(app)[1:]) == ['Vacation Photo 01.JPG', 'photo 2.jpg'],
              str(names(app)))
        check('面包屑有一层', len(payload['nav']['crumbs']) == 1 and payload['nav']['inside'])
        check('子目录内容没有被递归带出来', 'notes draft.TXT' not in names(app))
        check('所在目录列在目录内为空', all(r['dir'] == '' for r in app._rows()))

        # ---------------------------------------------------------- 改名
        step('执行改名（清理空格 + 转小写）')
        app.rename_now()
        after = sorted(os.listdir(root))
        check('文件已按规则改名', after == ['photo_2.jpg', 'sub_dir', 'vacation_photo_01.jpg'], str(after))
        check('文件夹也被改名', 'sub_dir' in after)
        check('产生了 3 条撤回记录', len(app.history) == 3, str(len(app.history)))

        # ---------------------------------------------------------- 进入改名后的目录
        step('进入改名后的子目录')
        app.reload()                                  # 仍在导入层内部，重新读盘
        app.enter_dir(next(i.tag for i in app.items if i.is_dir))
        check('子目录里的文件也在', names(app) == ['notes draft.TXT'], str(names(app)))
        app.rename_now()
        check('子目录文件改名生效',
              sorted(os.listdir(os.path.join(root, 'sub_dir'))) == ['notes_draft.txt'])

        # ---------------------------------------------------------- 撤回
        step('撤回全部改名（含所在目录本身被改回的情况）')
        app.revert()
        check('顶层还原成导入时的样子',
              set(os.listdir(root)) == {'sub dir', 'Vacation Photo 01.JPG', 'photo 2.jpg'},
              str(sorted(os.listdir(root))))
        check('子目录里的文件也还原',
              os.path.exists(os.path.join(root, 'sub dir', 'notes draft.TXT')))
        check('撤回后历史清空', app.history == [])
        check('当前目录消失后退回导入层', app.crumbs == [])

        # ---------------------------------------------------------- 大小写改名
        step('只改大小写的改名（Windows 上必须真的生效）')
        app.enter_dir(app.items[0].tag)
        app.workflow = [{'id': 'case', 'enabled': True,
                         'config': {'mode': 'upper', 'onlyBase': False}}]
        app.reload()
        app.rename_now()
        upper = sorted(os.listdir(root))
        check('主名与扩展名都变成大写', 'VACATION PHOTO 01.JPG' in upper, str(upper))
        app.revert()
        check('大小写改名也能撤回', 'Vacation Photo 01.JPG' in os.listdir(root))

        # ---------------------------------------------------------- 批内冲突
        step('批内重名拦截')
        conflict_dir = tempfile.mkdtemp(prefix='fr_conflict_')
        for name in ('a 1.txt', 'a 2.txt'):
            with open(os.path.join(conflict_dir, name), 'w', encoding='utf-8') as fh:
                fh.write('x')
        app2 = make_app([
            {'id': 'clean', 'enabled': True,
             'config': dict(rules.default_config('clean'), mode='underscore')},
            {'id': 'sequence', 'enabled': True,
             'config': dict(rules.default_config('sequence'), pos='replace',
                            template='same', digits=0)},
        ])
        app2.open_path(conflict_dir)
        payload = app2.rename_now()
        check('冲突时磁盘一个都没改',
              sorted(os.listdir(conflict_dir)) == ['a 1.txt', 'a 2.txt'],
              str(sorted(os.listdir(conflict_dir))))
        check('冲突项被标红', all(i.status == 'error' for i in app2.items))
        check('冲突时给出错误提示', payload['toast']['type'] == 'error', str(payload['toast']))
        shutil.rmtree(conflict_dir, ignore_errors=True)

        # ---------------------------------------------------------- 目标已存在
        step('目标文件已存在拦截')
        exist_dir = tempfile.mkdtemp(prefix='fr_exist_')
        for name in ('x.txt', 'y.txt'):
            with open(os.path.join(exist_dir, name), 'w', encoding='utf-8') as fh:
                fh.write('x')
        app3 = make_app([{'id': 'template', 'enabled': True,
                          'config': {'template': 'x.txt', 'start': 1, 'step': 1, 'digits': 0}}])
        app3.open_path(exist_dir)
        before = sorted(os.listdir(exist_dir))
        payload = app3.rename_now()
        check('已有同名文件时不动磁盘', sorted(os.listdir(exist_dir)) == before, str(before))
        blocked = [i for i in app3.items if i.status == 'error']
        check('被拦下的条目给出了原因', blocked and '已存在' in blocked[0].error)
        shutil.rmtree(exist_dir, ignore_errors=True)

        # ---------------------------------------------------------- 互换改名
        step('互换改名（a↔b 互相占用目标）')
        swap_dir = tempfile.mkdtemp(prefix='fr_swap_')
        for name in ('a.txt', 'b.txt'):
            with open(os.path.join(swap_dir, name), 'w', encoding='utf-8') as fh:
                fh.write(name)
        app4 = make_app()
        app4.open_path(swap_dir)
        manual = {}
        for item in app4.items:
            manual[item.tag] = 'b.txt' if item.name == 'a.txt' else 'a.txt'
        for item in app4.items:
            item.new_name = manual[item.tag]
        for item in app4.items:
            item.status = 'changed'
        app4.rename_now()
        with open(os.path.join(swap_dir, 'a.txt'), encoding='utf-8') as fh:
            check('a.txt 里装的是原来 b 的内容', fh.read() == 'b.txt')
        with open(os.path.join(swap_dir, 'b.txt'), encoding='utf-8') as fh:
            check('b.txt 里装的是原来 a 的内容', fh.read() == 'a.txt')
        shutil.rmtree(swap_dir, ignore_errors=True)

        # ---------------------------------------------------------- 移除不动磁盘
        step('从列表移除不影响磁盘')
        app5 = make_app()
        app5.add_paths([root])
        app5.enter_dir(app5.items[0].tag)
        count = len(app5.items)
        app5.remove_items([app5.items[0].tag])
        check('列表少了一项', len(app5.items) == count - 1)
        check('磁盘文件仍然齐全', len(os.listdir(root)) == 3, str(sorted(os.listdir(root))))

        print('\n全部 %d 项端到端断言通过' % len(PASSED))
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == '__main__':
    main()
