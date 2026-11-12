cmake_minimum_required(VERSION 3.24)

file(READ "${CMAKE_CURRENT_SOURCE_DIR}/../VERSION" FETCH_VERSION)
string(STRIP "${FETCH_VERSION}" FETCH_VERSION)
project(FetchPlugin VERSION ${FETCH_VERSION} LANGUAGES C CXX)

set(CMAKE_CXX_STANDARD 20)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)
if(MSVC)
    # one self-contained .vst3 without the VC++ runtime installer; sources are UTF-8
    set(CMAKE_MSVC_RUNTIME_LIBRARY "MultiThreaded$<$<CONFIG:Debug>:Debug>")
    add_compile_options(/utf-8)
endif()

include(FetchContent)
# Pinned release tarballs (with checksums) – no moving branches.
FetchContent_Declare(juce
    URL https://github.com/juce-framework/JUCE/archive/refs/tags/8.0.17-security.tar.gz
    URL_HASH SHA256=d37bab9f2330c50f42ba979575f6b55d5d3b23fa94221c2e57cac3abb69ac6d9)
FetchContent_Declare(signalsmith-linear
    URL https://github.com/Signalsmith-Audio/linear/archive/refs/tags/0.6.4.tar.gz
    URL_HASH SHA256=2cb10d84b96c626255ab46c4aa4c80f64ae532be487063cff6a08903c1445a56)
FetchContent_Declare(signalsmith-stretch
    URL https://github.com/Signalsmith-Audio/signalsmith-stretch/archive/refs/tags/1.4.0.tar.gz
    URL_HASH SHA256=077235709ecf2a358545e3ca7bdf32859f70f5c9afb4493e078bd753c9aff284)
FetchContent_MakeAvailable(juce signalsmith-linear signalsmith-stretch)

# The Fetch logo for the sidebar, embedded (BinaryData::fetchmark_png, fetchmarkdark_png)
juce_add_binary_data(FetchAssets SOURCES "${CMAKE_CURRENT_SOURCE_DIR}/../fetch-mark.png" "${CMAKE_CURRENT_SOURCE_DIR}/../fetch-mark-dark.png")

# Copy the .vst3 to ~/.vst3 (Linux) / %CommonProgramFiles%\VST3 (Windows) after building: -DFETCH_INSTALL_PLUGIN=ON
option(FETCH_INSTALL_PLUGIN "Copy the plugin to the user's VST3 folder after building" OFF)

juce_add_plugin(Fetch
    COMPANY_NAME "Fetch"
    COMPANY_WEBSITE "https://github.com/kazio01/fetch"
    BUNDLE_ID fi.samplehaku.FetchPlugin
    PLUGIN_MANUFACTURER_CODE Ftch
    PLUGIN_CODE Fsmp
    PRODUCT_NAME "Fetch"
    DESCRIPTION "Sample search with tempo- and key-synced preview"
    FORMATS VST3 Standalone
    IS_SYNTH TRUE
    NEEDS_MIDI_INPUT TRUE
    NEEDS_MIDI_OUTPUT FALSE
    EDITOR_WANTS_KEYBOARD_FOCUS TRUE
    VST3_CATEGORIES Instrument Sampler
    COPY_PLUGIN_AFTER_BUILD ${FETCH_INSTALL_PLUGIN})

target_sources(Fetch PRIVATE
    Source/FetchClient.cpp
    Source/Preview.cpp
    Source/PluginProcessor.cpp
    Source/PluginEditor.cpp
    Source/Theme.cpp
    Source/Sidebar.cpp
    Source/TagEditor.cpp)

target_compile_definitions(Fetch PUBLIC
    JUCE_WEB_BROWSER=0
    JUCE_USE_CURL=0
    JUCE_VST3_CAN_REPLACE_VST2=0
    JUCE_USE_MP3AUDIOFORMAT=1
    JUCE_DISPLAY_SPLASH_SCREEN=0
    FETCH_VERSION_STRING="${FETCH_VERSION}")

target_link_libraries(Fetch
    PRIVATE
        juce::juce_audio_utils
        juce::juce_audio_formats
        signalsmith-stretch
        FetchAssets
    PUBLIC
        juce::juce_recommended_config_flags
        juce::juce_recommended_lto_flags
        juce::juce_recommended_warning_flags)

# Offline tests (no host, no GUI window): build/FetchTests_artefacts/Release/FetchTests
option(FETCH_BUILD_TESTS "Build the FetchTests console app" ON)
if(FETCH_BUILD_TESTS)
    juce_add_console_app(FetchTests PRODUCT_NAME "FetchTests")
    target_sources(FetchTests PRIVATE
        tests/FetchTests.cpp
        Source/FetchClient.cpp
        Source/Preview.cpp
        Source/PluginProcessor.cpp
        Source/PluginEditor.cpp
        Source/Theme.cpp
        Source/Sidebar.cpp
    Source/TagEditor.cpp)
    target_compile_definitions(FetchTests PRIVATE
        JUCE_WEB_BROWSER=0 JUCE_USE_CURL=0 JUCE_USE_MP3AUDIOFORMAT=1 JUCE_MODAL_LOOPS_PERMITTED=1 JUCE_PLUGINHOST_VST3=1
        FETCH_VERSION_STRING="${FETCH_VERSION}")
    target_link_libraries(FetchTests PRIVATE
        juce::juce_audio_utils juce::juce_audio_formats signalsmith-stretch FetchAssets
        juce::juce_recommended_config_flags)
endif()
