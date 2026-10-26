# Steam Audio — Linux arm64 / Windows arm64 지원 계획

> 대상: Steam Audio 4.8.1 → **4.9.0**
> 상태: 계획(코드 미작성). 승인 후 아래 순서대로 진행.

---

## 0. 버전 결정: **4.9.0**

- 새 타깃 플랫폼 두 개를 추가하는 것은 **기능 추가**이므로 SemVer상 minor bump가 맞다. 4.8.2는 버그 수정 릴리스로 읽힌다.
- 공개 API(`phonon.h`)와 ABI는 바꾸지 않는다. `IPL_SIMDLEVEL_NEON`은 이미 있고, `STEAMAUDIO_VERSION`만 4.9.0이 된다. 그래서 4.x 호출자는 그대로 호환된다.
- 갱신할 위치(9곳): `core/`, `unity/`, `fmod/`, `wwise/`, `unreal/`의 `CMakeLists.txt`에 있는 `project(... VERSION)`, `unity|fmod|unreal/setup.py`, `README.md`.
  - `.uplugin`의 `VersionName`은 지금도 4.7.0으로 어긋나 있다. 이번에 4.9.0으로 함께 맞춘다.

---

## 1. 현재 상태 조사 결과

### 이미 준비돼 있는 것

| 항목 | 위치 | 비고 |
|---|---|---|
| NEON `float4` 백엔드 | `core/src/core/neon_float4.h` | Android/iOS/macOS-arm64에서 이미 쓰는 코드 |
| ARM용 `array_math`(복소수 곱 등) | `core/src/core/array_math.cpp` (`IPL_CPU_ARM64` 분기) | |
| BVH 표면적 ARM 분기 | `core/src/core/bvh.h` | `_mm_shuffle_ps`를 우회함 |
| CPU 감지(core만) | `core/CMakeLists.txt` | Windows는 `CMAKE_GENERATOR_PLATFORM==ARM64`, Linux는 `aarch64` |
| lib/bin 경로 헬퍼 | `core/build/SteamAudioHelpers.cmake`, `wwise/build/...` | `windows-arm64`, `linux-arm64`가 있음 |
| 빌드 스크립트 | `core/build/build.py` | `-A ARM64`와 Linux arm64 toolchain 인자가 있음 |
| 의존성 스크립트 | `core/build/get_dependencies.py`, `dependencies.json` | `windows-arm64`, `linux-arm64` configure 분기가 있음 |
| 크로스 툴체인 | `core/build/toolchain_linux_arm64.cmake` | `gcc-9`와 `/sysroot`가 하드코딩돼 있음 |

정리하면 **골격은 절반쯤 있지만, 끝까지 연결된 경로는 없다.**

### 빠져 있거나 고장 난 것

1. **CPack / install 목록**에 `lib/windows-arm64`, `lib/linux-arm64`, `symbols/...`가 없다. 빌드는 돼도 패키지에 포함되지 않는다.
2. **Linux arm64 디버그 심볼 분리**(`objcopy`/`strip`)가 `NOT IPL_CPU_ARMV8`로 꺼져 있다. 크로스 빌드에서 호스트의 objcopy를 쓰기 때문이다. 같은 이유로 `libphonon.so.dbg` install도 빠져 있다.
3. **통합 플러그인 복사 경로**: core의 post-build 단계에서 FMOD/Unity/Unreal 복사 디렉터리를 정할 때 arm64 분기가 없다.
4. **unity/fmod/wwise의 CMake CPU 감지**가 `Windows|Linux → 포인터 크기 8이면 X64`로 되어 있다. 그래서 arm64가 **x64로 오인**된다.
5. **unity/fmod의 `SteamAudioHelpers.cmake`**에 arm64 분기가 없다.
6. **`get_host_bin_subdir` 및 `get_dependencies.py`의 host 판정**이 Linux는 항상 `linux-x64`, Windows는 항상 `windows-x64`로 고정돼 있다. arm64 Linux 호스트에서 네이티브로 빌드하면 x64용 `flatc`를 찾다가 실패한다.
7. **PFFFT를 windows-arm64로 빌드할 때**:
   - `dependencies.json`이 `TARGET_C_ARCH=armv8-a`를 넘긴다. PFFFT는 MSVC에서 이를 `/arch:armv8-a`로 바꾸는데, MSVC가 모르는 옵션이다(MSVC 형식은 `armv8.0`).
   - PFFFT의 NEON 감지(`simd/pf_neon_float.h`)는 `__arm__|__aarch64__|__arm64__`만 본다. MSVC는 `_M_ARM64`만 정의하므로 **스칼라 경로로 떨어져 성능이 크게 손해**를 본다.
8. **Denormal 처리**: `Context::setDenormalsAreZeroes()`는 IPP 전용이라 ARM에서는 아무 일도 하지 않는다. 리버브와 IIR의 꼬리 부분에서 denormal이 생기면 성능이 떨어진다.
9. **`float4.h` 연산자 오버로드**가 `IPL_OS_WINDOWS` 조건으로 묶여 있다. MSVC ARM64에서는 `__n128`이므로 이 오버로드가 필요하고, 그 자체로는 문제가 없다. 다만 정확한 조건은 OS가 아니라 컴파일러(`IPL_COMPILER_MSVC`)다. clang-cl은 벡터 연산자가 내장돼 있어 이 오버로드가 컴파일 오류를 낸다.
10. **Unity 플러그인 `.meta`**: `Windows/x86_64/*.dll.meta`가 `Standalone: Win64 → CPU: AnyCPU`로 되어 있다. ARM64 DLL을 추가하면 같은 이름의 플러그인이 둘이 되어 충돌한다.
11. **Unreal `Build.cs` / `.uplugin`**: `Win64`는 무조건 x64 lib을 쓰고, `LinuxArm64` 플랫폼이 없다.

---

## 2. 설계 결정: ARM64 백엔드 구성

ARM64 경로는 Android-arm64나 macOS-arm64 슬라이스와 **같은 구성**으로 맞춘다. 이미 출하 중인 경로를 재사용하므로 위험이 가장 낮다.

| 기능 | x64 (현재) | **arm64 (계획)** |
|---|---|---|
| SIMD | SSE/AVX (`float4`/`float8`) | NEON `float4`. `float8`/AVX는 쓰지 않는다 |
| FFT | IPP | **PFFFT (NEON)** |
| 벡터 수학 | IPP | 내장 `array_math.cpp` (NEON) |
| 레이 트레이싱 | Embree + ISPC | **Steam Audio 내장 BVH 트레이서** |
| GPU (Radeon Rays / TAN) | Windows x64만 | 쓰지 않음 |
| Denormal | IPP FTZ/DAZ | **FPCR.FZ 비트를 직접 설정** (신규) |

Embree와 ISPC는 이번 범위에서 **제외**한다. ISPC가 `1.12 EXACT`로 고정돼 있는데, aarch64 타깃(`neon-i32x4`)은 1.13 이상에서만 안정적이다. Embree 4는 aarch64를 지원하지만 ISPC 커널과 빌드 매트릭스를 함께 손봐야 하므로 후속 작업(Phase 6, 선택)으로 미룬다. 소스 쪽 `#if ... IPL_CPU_X86 || IPL_CPU_X64` 가드가 이미 있어서 코드 변경은 필요 없다.

**ARM64EC는 지원하지 않는다.** ARM64EC는 `_M_X64`를 정의하므로 현재 코드는 이를 x64로 인식해 SSE 경로를 탄다. 네이티브 ARM64(`-A ARM64`)만 대상으로 하고, 문서에 이 점을 명시한다.

---

## 3. 작업 항목

### Phase 1 — core 빌드 시스템

| # | 파일 | 변경 |
|---|---|---|
| 1.1 | `core/CMakeLists.txt` | CPU 감지를 보강한다. Windows는 `CMAKE_GENERATOR_PLATFORM` 외에 `CMAKE_CXX_COMPILER_ARCHITECTURE_ID STREQUAL "ARM64"`도 본다(Ninja + `vcvarsall arm64` 대응). Linux는 `aarch64` 외에 `arm64`도 받는다. |
| 1.2 | `core/CMakeLists.txt` | CPack `CPACK_INSTALLED_DIRECTORIES`에 `lib/windows-arm64`, `lib/linux-arm64`, `symbols/windows-arm64`, `symbols/linux-arm64`를 추가한다. |
| 1.3 | `core/src/core/CMakeLists.txt` | 디버그 심볼 분리에 `objcopy`/`strip` 대신 `${CMAKE_OBJCOPY}`/`${CMAKE_STRIP}`를 쓴다. 크로스 빌드에서도 맞는 도구를 쓰게 되므로 `NOT IPL_CPU_ARMV8` 예외를 제거하고, `.dbg` install도 arm64에 켠다. |
| 1.4 | `core/src/core/CMakeLists.txt` | FMOD/Unity/Unreal post-build 복사 경로에 arm64 분기를 추가한다(경로 이름은 Phase 3 표 참고). |
| 1.5 | `core/build/toolchain_linux_arm64.cmake` | `gcc-9`와 `/sysroot` 하드코딩을 없앤다. 컴파일러는 `aarch64-linux-gnu-gcc/g++`(버전 접미사 없음)를 기본으로 하고 `STEAMAUDIO_ARM64_SYSROOT` 캐시 변수나 환경 변수로 바꿀 수 있게 한다. sysroot가 없으면 `CMAKE_SYSROOT`를 설정하지 않는다(Debian/Ubuntu multiarch 패키지로 충분). |
| 1.6 | `core/build/build.py` | host CPU를 `platform.machine()`으로 판정한다. **arm64 호스트에서 `-a arm64`면 toolchain 없이 네이티브로 빌드**하고, x64 호스트일 때만 크로스 toolchain을 넣는다. `find_tool`의 host 서브디렉터리도 arm64를 인식하게 한다. |
| 1.7 | `core/build/SteamAudioHelpers.cmake` (+ wwise 사본) | `get_host_bin_subdir`가 `CMAKE_HOST_SYSTEM_PROCESSOR`를 보고 `linux-arm64` / `windows-arm64`를 반환하게 한다. |
| 1.8 | `core/build/get_dependencies.py` | host platform 판정에 arm64를 추가한다(`flatc` 같은 tool 의존성용). arm64 Windows 호스트에서 tool이 없을 때 `windows-x64` tool로 폴백할지는 Windows 11 x64 에뮬레이션을 전제로 결정한다. |
| 1.9 | `core/build/dependencies.json` | PFFFT 플래그에서 `windows-arm64`를 `linux-arm64` 그룹과 분리한다. MSVC에는 `TARGET_*_ARCH=none`과 `-DCMAKE_C_FLAGS=/DPFFFT_ENABLE_NEON`을 넘긴다. |
| 1.10 | `core/build/pffft.patch` (신규) + `dependencies.json`의 `"patch"` | `simd/pf_neon_float.h`의 감지 조건에 `|| defined(_M_ARM64)`를 추가한다. MSVC에서 PFFFT NEON 코드가 컴파일되지 않으면(예: `(v4sf*)` 캐스트, 벡터 이니셜라이저) 해당 부분만 패치한다. 그래도 안 되면 **스칼라로 폴백하고 그 사실을 문서화**한다. |

### Phase 2 — core 소스

| # | 파일 | 변경 |
|---|---|---|
| 2.1 | `core/src/core/float4.h` | 연산자 오버로드 조건을 `IPL_OS_WINDOWS`에서 `IPL_COMPILER_MSVC`로 바꾼다. MSVC x64/ARM64 동작은 그대로이고, clang-cl에서는 깨지지 않게 된다. |
| 2.2 | `core/src/core/neon_float4.h` | MSVC ARM64 호환성을 점검한다. MSVC에서는 모든 NEON 타입이 `__n128` 하나의 별칭이므로 오버로드 충돌과 brace-init 사용 여부를 확인한다. `vdupq_n_s32(1 << 31)`는 부호 있는 정수 오버플로이므로 `INT32_MIN`으로 바꾼다. |
| 2.3 | `core/src/core/context.cpp` | `setDenormalsAreZeroes()`에 ARM64 분기를 추가한다. GCC/Clang에서는 `mrs/msr fpcr` 인라인 어셈으로 FZ 비트(bit 24)를 설정하고, MSVC에서는 `_ReadStatusReg/_WriteStatusReg(ARM64_FPCR)`를 쓴다. **적용 범위는 `IPL_OS_WINDOWS`/`IPL_OS_LINUX`의 ARM64로 한정**해서 기존 Android/iOS/macOS 동작은 바꾸지 않는다(확장 여부는 별도로 결정). |
| 2.4 | `core/src/core/vdsp_fft.cpp`, `vdsp_array_math.cpp` | 방어적 수정. 가드를 `IPL_CPU_ARM64`에서 `IPL_OS_MACOSX && IPL_CPU_ARM64`로 좁힌다. 지금은 CMake가 macOS에서만 넣지만, 소스 glob이나 unity build에서 Linux/Windows arm64가 Accelerate를 include하는 사고를 막기 위해서다. |
| 2.5 | `core/src/core/platform.h` | `_M_ARM64EC`를 감지하면 `#error "ARM64EC is not supported; build native ARM64"`를 낸다. 잘못된 SSE 경로로 조용히 빌드되는 일을 막는다. |
| 2.6 | 테스트/벤치마크 | Embree 관련 코드는 이미 x86 가드가 있어 변경이 없다. `IPL_SIMDLEVEL_AVX512` 요청은 `min()`으로 클램프되므로 ARM에서도 안전하다(확인만 한다). |

### Phase 3 — 통합 플러그인

공통 작업으로 `unity/`, `fmod/`, `wwise/`의 `CMakeLists.txt` CPU 감지를 core와 같은 로직으로 바꾼다. 또 unity/fmod의 `SteamAudioHelpers.cmake`를 core 버전과 동기화한다.

플러그인별 출력 경로는 다음과 같이 계획한다(각 엔진/SDK의 관례를 따른다).

| 통합 | Windows arm64 | Linux arm64 | 비고 |
|---|---|---|---|
| core 설치 | `lib/windows-arm64` | `lib/linux-arm64` | 이미 헬퍼에 있음 |
| Unity 플러그인 | `Binaries/Windows/ARM64/` | `Binaries/Linux/ARM64/` | Unity 6 기준이며 `.meta`에 `CPU: ARM64`를 쓴다 |
| Unity FMOD | `Plugins/FMOD/platforms/win/lib/arm64` | `Plugins/FMOD/platforms/linux/lib/arm64` | FMOD for Unity의 디렉터리 관례 |
| Unity Wwise | `Wwise/API/Runtime/Plugins/Windows/ARM64/DSP` | `.../Linux/ARM64/DSP` | Wwise Unity 통합 버전별로 확인 필요 |
| Unreal SDK | `SteamAudioSDK/lib/windows-arm64` | `SteamAudioSDK/lib/linux-arm64` | |
| Unreal Wwise | `ARM64_vc170` | `Linux_aarch64` | Wwise SDK 이름은 실제 SDK로 확인 필요 |

| # | 파일 | 변경 |
|---|---|---|
| 3.1 | `unity/src/native/CMakeLists.txt` | `PLUGIN_DIR`에 arm64 분기를 추가하고 심볼 분리 예외(`NOT IPL_CPU_ARMV8`)를 1.3과 같은 방식으로 정리한다. |
| 3.2 | Unity `.meta` | `Windows/x86_64/*.meta`의 Win64 CPU를 `AnyCPU`에서 `x86_64`로 바꾼다. `Windows/ARM64`, `Linux/ARM64` 폴더와 `.meta`(`CPU: ARM64`, 에디터 포함)를 새로 만든다. 바이너리는 저장소에 넣지 않는다(기존 관례). |
| 3.3 | `unity/.../Scripts/Editor/Build.cs` | FMOD/Wwise 플러그인 경로 목록에 arm64 항목을 추가한다. |
| 3.4 | `fmod/src/CMakeLists.txt` | Unity/Unreal FMOD 출력 경로에 arm64 분기를 추가하고 심볼 분리 예외를 정리한다. `FindFMOD.cmake`에서 FMOD API의 `lib/arm64`를 찾게 한다. |
| 3.5 | `wwise/src/CMakeLists.txt` | `wp.py build ... -x ARM64`와 Linux aarch64 빌드/복사 분기를 추가한다. **Wwise SDK가 해당 플랫폼을 실제로 제공하는지는 SDK 버전에 따라 다르다.** 따라서 best-effort로 두고, 없으면 configure 시 경고 후 건너뛴다. |
| 3.6 | `unreal/.../SteamAudioSDK.Build.cs` | `Win64`에서 `Target.Architecture == UnrealArch.Arm64`(`#if UE_5_2_OR_LATER`)이면 `windows-arm64` lib/dll을 쓰고, TAN/GPUUtilities DLL은 제외한다. `UnrealTargetPlatform.LinuxArm64` 분기를 추가한다. |
| 3.7 | `unreal/.../*.uplugin` | `WhitelistPlatforms`에 `"LinuxArm64"`를 추가한다. Win64는 이미 있다. |

### Phase 4 — 버전·문서

| # | 파일 | 변경 |
|---|---|---|
| 4.1 | §0의 9곳과 `.uplugin` | 4.9.0으로 올린다. |
| 4.2 | `README.md`, `core/git/README.md.in` | 지원 플랫폼에 "Windows (x86, x64, arm64), Linux (x86, x64, arm64)"를 넣는다. |
| 4.3 | `core/doc/build-instructions.rst` | `-a arm64` 설명(Windows/Linux)을 추가하고, 크로스 빌드 방법(toolchain/sysroot)과 ARM64에서 꺼지는 기능(IPP/Embree/GPU), ARM64EC 미지원을 적는다. |

### Phase 5 — 검증 및 CI

이 컨테이너는 **x86_64 Linux**이고 MSVC가 없다. 그래서 검증을 두 단계로 나눈다.

**로컬(이 컨테이너)**
1. `apt`로 `g++-aarch64-linux-gnu`와 `qemu-user-static`을 설치한다(네트워크 정책이 허용할 때).
2. `get_dependencies.py -p linux -a arm64`로 크로스 빌드하고 `build.py -p linux -a arm64 --minimal`을 돌린다.
3. 단위 테스트(Catch)를 `qemu-aarch64 -L /usr/aarch64-linux-gnu`로 실행한다.
4. 회귀 확인: 같은 브랜치에서 linux-x64 빌드와 테스트가 그대로 통과하는지 본다.
5. Windows arm64는 로컬에서 부분적으로만 확인한다. `clang --target=aarch64-pc-windows-msvc -fsyntax-only`로 core를 문법 검사한다. clang의 `arm_neon.h`는 MSVC의 `__n128` 특성을 재현하지 못하므로 **최종 확인은 CI**에서 한다.

**CI(신규 `.github/workflows/arm64.yml`)**
- `ubuntu-24.04-arm` 네이티브 job: 의존성 → 빌드 → `ctest`
- `windows-11-arm` 네이티브 job: VS2022 `-A ARM64`로 의존성 → 빌드 → `ctest`
- `ubuntu-latest`(x64) job: aarch64 크로스 빌드 스모크 테스트(toolchain 파일 검증용)
- 기존 x64 경로의 회귀를 막기 위해 linux-x64 `--minimal` 빌드 job도 하나 둔다.

**완료 조건**: 두 arm64 네이티브 job에서 core 단위 테스트가 전부 통과하고, `cpack` 결과 zip에 `lib/linux-arm64/libphonon.so`와 `lib/windows-arm64/phonon.dll`이 들어 있다.

### Phase 6 — (선택, 후속) 고급 가속

- Embree 4 + ISPC ≥ 1.13(aarch64 `neon-i32x4` 타깃)로 ARM64 레이 트레이싱 가속
- 리버브와 바이노럴 성능을 x64 IPP와 비교하는 벤치마크(`benchmark/`)

---

## 4. 위험 요소와 열린 질문

| 위험 | 영향 | 대응 |
|---|---|---|
| MSVC ARM64 NEON의 특이점(모든 타입이 `__n128`) | Windows arm64 컴파일 오류 | CI `windows-11-arm`에서 확인. `neon_float4.h`는 이미 `float32x4_t` 하나만 쓰므로 위험은 낮음 |
| PFFFT NEON이 MSVC에서 안 될 수 있음 | 성능 저하(스칼라 FFT) | 패치를 시도하고, 실패하면 스칼라 폴백을 문서화 |
| IPP가 없어서 생기는 성능 차이 | x64 대비 FFT/벡터 연산이 느림 | Android-arm64와 같은 수준. Phase 6에서 벤치마크 |
| Unity/Unreal/FMOD/Wwise의 arm64 경로·이름 관례 | 플러그인이 로드되지 않음 | 각 SDK 최신 버전 문서로 확인. 실제 엔진에서의 로드 테스트는 이 환경에서 불가 |
| Wwise SDK의 arm64 플랫폼 제공 여부 | Wwise 통합 일부가 빌드되지 않음 | best-effort로 두고, 없으면 skip |
| 바이너리 배포 | 저장소에는 소스와 빌드 스크립트만 있음 | 기존 관례대로 바이너리는 커밋하지 않음 |

---

## 5. 커밋 단위(예정)

1. `build: detect arm64 on Windows/Linux hosts and targets` (Phase 1.1, 1.5–1.8)
2. `deps: build PFFFT with NEON on windows-arm64` (1.9–1.10)
3. `core: ARM64 denormal flush, MSVC operator guard, ARM64EC guard` (Phase 2)
4. `build: package and strip linux/windows arm64 binaries` (1.2–1.4)
5. `integrations: arm64 output paths for Unity/FMOD/Wwise/Unreal` (Phase 3)
6. `ci: add native arm64 build+test workflows` (Phase 5)
7. `Bump version to 4.9.0; document arm64 support` (Phase 4)

---

## 6. 진행 결과 (구현 후 갱신)

### 계획과 달라진 점

| 항목 | 내용 |
|---|---|
| 2.3 Denormal (FPCR.FZ) | **제외**. `Context::setDenormalsAreZeroes()`는 x64를 포함해 어디서도 호출되지 않는 죽은 코드였다. ARM64 본문을 추가해도 효과가 없고, 라이브러리가 호스트 오디오 스레드의 FPCR을 바꾸는 것도 바람직하지 않다. x64와 동일하게 denormal을 flush하지 않는다. |
| 1.9 PFFFT 플래그 | windows-arm64는 별도 플래그 그룹 없이 `armv8-a` 그룹에서 빠지기만 했다. NEON 활성화는 `pffft.patch`가 `_M_ARM64`에서 자동으로 켠다. |
| 3.4 `FindFMOD.cmake` | 변경 불필요. FMOD 통합은 헤더만 쓰고 FMOD 라이브러리를 링크하지 않는다. |
| (추가) NEON `float4::round` 버그 수정 | 기존 구현은 `x ± 0.5`만 반환하고 정수로 자르지 않았다. 그래서 `HRTFDatabase::wrapPhase`가 **모든 ARM 빌드**(Android/iOS/macOS-arm64 포함)에서 틀린 위상을 냈다. ARM64는 `vrndnq_f32`(ties-to-even, SSE와 동일), ARMv7은 truncate를 추가했다. 기존 ARM 플랫폼의 출력도 (올바른 쪽으로) 바뀐다. |
| (추가) flatbuffers GCC 13 | Ubuntu 24.04(GCC 13)에서 `-Werror=stringop-overflow`로 빌드가 실패해서 Linux에 `-Wno-error=stringop-overflow`를 추가했다. arm64와 무관한 기존 문제지만 arm64 네이티브 CI 러너도 GCC 13이다. |
| (추가) 크로스 툴체인 find root | sysroot가 없으면 `/usr/aarch64-linux-gnu`를 `CMAKE_FIND_ROOT_PATH`로 쓴다. 이게 없으면 mysofa의 `find_library(m)`이 실패한다. |
| (추가) Unreal 런타임 로더 | `SteamAudioModule.cpp`, `SteamAudioFMODStudio.cpp`, `SteamAudioWwise.cpp`가 x64 경로를 하드코딩하고 있어서 `PLATFORM_CPU_ARM_FAMILY` 분기를 추가했다. |
| (추가) Unity FMOD 레이아웃 이동 | `SteamAudioManager.cs`의 FMOD 2.1↔2.2 레이아웃 이동에 arm64 파일을 추가했다. 파일이 있을 때만 이동하므로 arm64가 없는 패키지도 깨지지 않는다. |
| Unreal 패키징 파이프라인 | `unreal/src/CMakeLists.txt`(UBT로 x64 타깃을 빌드하는 릴리스 패키징)는 바꾸지 않았다. arm64 UE 툴체인이 있는 패키징 머신이 필요하다. |

### 검증 결과 (이 컨테이너, x86_64 호스트)

- linux-arm64 크로스 빌드(GCC 13 aarch64): 의존성 전체와 core 전체(테스트·벤치마크·샘플 포함)가 성공했다.
- 단위 테스트를 qemu-aarch64로 실행한 결과 **146 test cases / 894 assertions 전부 통과**했다.
- `libphonon.so`가 aarch64로 strip되었고, `.gnu_debuglink`로 `libphonon.so.dbg`에 연결되는 것을 확인했다.
- `float4::round`: 수정 전에는 12/12 오답이었고 수정 후에는 0/12 오답(`nearbyintf` 기준)이다. ARMv7 경로는 `roundf` 기준 0/12 오답이다.
- Unity native plugin(`libaudioplugin_phonon.so`)이 aarch64로 크로스 빌드되는 것을 확인했다.
- FMOD 플러그인 CMake를 arm64로 configure해서 Unity/Unreal 출력 경로를 확인했다(FMOD SDK가 없어서 빌드는 하지 않음).
- Windows ARM64: clang `aarch64-pc-windows-msvc`로 플랫폼 감지와 `float4` 헤더 문법만 확인했다. `arm64ec` 타깃에서는 의도한 `#error`가 나는 것도 확인했다. **MSVC 빌드와 테스트는 CI(`windows-11-arm`)에서 확인해야 한다.**
- Wwise: SDK가 없어서 configure도 할 수 없다. 검증하지 못했다.
- x64 회귀 확인: **원래 `master`도 GCC 13(Ubuntu 24.04)에서 x64 빌드가 실패한다.** x64 전용 `-fabi-version=6`(AVX용 플래그)와 libstdc++ 13이 충돌하기 때문이고, 이번 변경과는 무관하다. AVX를 끄고 빌드하면 이 브랜치의 x64 빌드와 단위 테스트가 통과한다. CI의 x64 job은 ubuntu-22.04(GCC 11)를 쓰도록 했다(아직 실행해 보지 않음).

### 커밋

1. `build: Support building core for Linux arm64 and Windows arm64`
2. `core: Fix NEON float4::round, and harden platform guards for arm64`
3. `integrations: Add Linux arm64 and Windows arm64 support`
4. `ci: Build and test core on native arm64 Linux and Windows`
5. `Bump version to 4.9.0, and document arm64 support`
