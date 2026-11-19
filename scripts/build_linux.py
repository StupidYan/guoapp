import os
import shutil
import subprocess
import sys
from pathlib import Path

from app_build import app

root = Path(__file__).resolve().parents[1]
env = os.environ.copy()
env.setdefault('GOPROXY', 'https://goproxy.cn,direct')
env.setdefault('GOSUMDB', 'off')
flutter = shutil.which('flutter')
if not flutter:
    raise SystemExit('请先将 Flutter SDK 的 bin 目录加入 PATH。')
CORE_MARKER = '# duanju linux core library'
# 某些沙箱环境（如 IDE 内嵌终端）里 sys.executable 指向宿主 AppImage
# 而非真正的解释器，优先用 PATH 里的 python3 调子脚本。
python = shutil.which('python3') or sys.executable


def build_vidstab():
    '''编译 libvidstab.so.1.1（Fedora 仓库无此包，ffmpeg_kit 捆绑的 libavfilter 依赖）。'''
    cache = root / 'build' / 'third-party' / 'vid.stab'
    output = cache / 'build' / 'libvidstab.so.1.1'
    if output.is_file():
        return output
    source = cache / 'src'
    if not (source / 'CMakeLists.txt').is_file():
        subprocess.run(['git', 'clone', '--depth', '1', '--branch', 'v1.1.0',
                        'https://github.com/georgmartius/vid.stab.git', str(source)], check=True)
    subprocess.run(['cmake', '-S', str(source), '-B', str(cache / 'build'),
                    '-DCMAKE_BUILD_TYPE=Release', '-DBUILD_SHARED_LIBS=ON',
                    '-DCMAKE_POLICY_VERSION_MINIMUM=3.5'], check=True)
    subprocess.run(['cmake', '--build', str(cache / 'build'),
                    '-j', str(os.cpu_count() or 2)], check=True)
    if not output.is_file():
        raise SystemExit('vid.stab 编译失败：' + str(output))
    return output


def build_x265():
    '''编译 libx265.so.199（Fedora 44 自带 x265 4.x soname 不匹配插件所需的 3.5/199）。'''
    cache = root / 'build' / 'third-party' / 'x265'
    output = cache / 'build' / 'libx265.so.199'
    if output.is_file():
        return output
    source = cache / 'source'
    if not (source / 'CMakeLists.txt').is_file():
        subprocess.run(['git', 'clone', '--depth', '1', '--branch', '3.5',
                        'https://bitbucket.org/multicoreware/x265_git.git', str(source)], check=True)
    cmake_file = source / 'CMakeLists.txt'
    cmake_text = cmake_file.read_text(encoding='utf-8')
    for policy in ('cmake_policy(SET CMP0025 OLD)\n', 'cmake_policy(SET CMP0054 OLD)\n'):
        cmake_text = cmake_text.replace(policy, '')
    cmake_file.write_text(cmake_text, encoding='utf-8')
    subprocess.run(['cmake', '-S', str(source), '-B', str(cache / 'build'),
                    '-DENABLE_SHARED=ON', '-DENABLE_CLI=OFF', '-DENABLE_ASSEMBLY=OFF',
                    '-DCMAKE_BUILD_TYPE=Release', '-DCMAKE_POLICY_VERSION_MINIMUM=3.5'],
                   check=True)
    subprocess.run(['cmake', '--build', str(cache / 'build'),
                    '-j', str(os.cpu_count() or 2)], check=True)
    if not output.is_file():
        raise SystemExit('x265 编译失败：' + str(output))
    return output


def fixup_bundle():
    '''补齐 ffmpeg_kit 运行库并修正 rpath；须在 flutter build 后、打包前执行。'''
    bundle = root / 'build' / 'linux' / 'x64' / 'release' / 'bundle'
    lib = bundle / 'lib'
    if not lib.is_dir():
        raise SystemExit('缺少 Linux bundle：' + str(bundle))
    patchelf = shutil.which('patchelf')
    if not patchelf:
        raise SystemExit('需要 patchelf 修正运行库搜索路径（Fedora：sudo dnf install patchelf）。')
    shutil.copy2(build_vidstab(), lib / 'libvidstab.so.1.1')
    shutil.copy2(build_x265(), lib / 'libx265.so.199')
    subprocess.run([patchelf, '--force-rpath', '--set-rpath', '$ORIGIN/lib',
                    str(bundle / 'duanju_app')], check=True)
    plugin = lib / 'libffmpeg_kit_flutter_new_min_gpl_plugin.so'
    if plugin.is_file():
        subprocess.run([patchelf, '--force-rpath', '--set-rpath', '$ORIGIN', str(plugin)], check=True)


def ensure_linux_project():
    '''首次运行时生成官方 Linux 模板，并注入 Go 核心安装规则。'''
    top = root / 'linux' / 'CMakeLists.txt'
    if not top.is_file():
        subprocess.run([flutter, 'create', '--platforms=linux', '.'],
                       cwd=root, env=env, check=True)
    if not top.is_file():
        raise SystemExit('Flutter 未生成 linux/CMakeLists.txt，模板可能已变化。')

    top_cmake = top.read_text(encoding='utf-8')
    if CORE_MARKER not in top_cmake:
        top_cmake += ('\n' + CORE_MARKER + '''
# 安装 Go 核心（由 scripts/build_native.py 生成）到 bundle/lib，
# 与 Flutter Linux 默认 rpath=$ORIGIN/lib 保持一致。
install(FILES "${CMAKE_CURRENT_SOURCE_DIR}/runner/libduanju_core.so"
  DESTINATION "${INSTALL_BUNDLE_LIB_DIR}" COMPONENT Runtime)
''')
        top.write_text(top_cmake, encoding='utf-8')


ensure_linux_project()
subprocess.run([python, str(root / 'scripts' / 'build_native.py'), '--platform', 'linux'],
               cwd=root, env=env, check=True)
subprocess.run([flutter, 'pub', 'get', '--enforce-lockfile'], cwd=root, env=env, check=True)
subprocess.run([flutter, 'build', 'linux', '--release', '--no-pub', *app.flutter_arguments], cwd=root, env=env, check=True)
fixup_bundle()
subprocess.run([python, str(root / 'scripts' / 'package_release.py'), '--platform', 'linux'],
               cwd=root, env=env, check=True)
