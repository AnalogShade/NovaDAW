/*
 * core/bin/vst3_bridge_x64.cpp
 * Standalone 64-bit (x64) VST3 Host Bridge for NovaDAW on Windows.
 * Uses Steinberg VST 3.8+ pluginterfaces (MIT License).
 * Compiled statically (/MT) with MSVC x64 for zero external runtime dependencies.
 */
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <windows.h>
#include <objbase.h>
#include <io.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>

#include <atomic>
#include <algorithm>
#include <map>
#include <mutex>
#include <string>
#include <vector>

#define INIT_CLASS_IID
#include "pluginterfaces/base/funknown.h"
#include "pluginterfaces/base/ipluginbase.h"
#include "pluginterfaces/base/ibstream.h"
#include "pluginterfaces/base/ustring.h"
#include "pluginterfaces/gui/iplugview.h"
#include "pluginterfaces/vst/ivstcomponent.h"
#include "pluginterfaces/vst/ivstaudioprocessor.h"
#include "pluginterfaces/vst/ivsteditcontroller.h"
#include "pluginterfaces/vst/ivstevents.h"
#include "pluginterfaces/vst/ivstparameterchanges.h"
#include "pluginterfaces/vst/ivsthostapplication.h"
#include "pluginterfaces/vst/ivstmessage.h"
#include "pluginterfaces/vst/ivstattributes.h"
#include "pluginterfaces/vst/ivstpluginterfacesupport.h"
#include "pluginterfaces/vst/ivstprocesscontext.h"
#include "pluginterfaces/vst/ivstmidicontrollers.h"

using namespace Steinberg;
using namespace Steinberg::Vst;

#pragma pack(push, 1)
struct MidiMsgRecord {
    int32_t sample_idx;
    uint8_t b0;
    uint8_t b1;
    uint8_t b2;
    uint8_t pad;
};
#pragma pack(pop)

/* -------------------------------------------------------------------------- */
/* Low-level binary pipe helpers                                              */
/* -------------------------------------------------------------------------- */

static bool read_exact(int fd, void* dst, size_t len) {
    uint8_t* p = (uint8_t*)dst;
    size_t got = 0;
    while (got < len) {
        int r = _read(fd, p + got, (unsigned int)(len - got));
        if (r <= 0) return false;
        got += (size_t)r;
    }
    return true;
}

static bool write_exact(int fd, const void* src, size_t len) {
    const uint8_t* p = (const uint8_t*)src;
    size_t sent = 0;
    while (sent < len) {
        int w = _write(fd, p + sent, (unsigned int)(len - sent));
        if (w <= 0) return false;
        sent += (size_t)w;
    }
    return true;
}

static std::string wide_to_utf8(const wchar_t* ws) {
    if (!ws || !ws[0]) return "";
    int needed = WideCharToMultiByte(CP_UTF8, 0, ws, -1, nullptr, 0, nullptr, nullptr);
    if (needed <= 1) return "";
    std::string out((size_t)(needed - 1), '\0');
    WideCharToMultiByte(CP_UTF8, 0, ws, -1, &out[0], needed, nullptr, nullptr);
    return out;
}

static std::string tchar_to_utf8(const TChar* ts, int maxChars) {
    if (!ts) return "";
    int len = 0;
    while (len < maxChars && ts[len] != 0) ++len;
    if (len == 0) return "";
    int needed = WideCharToMultiByte(CP_UTF8, 0, (const wchar_t*)ts, len, nullptr, 0, nullptr, nullptr);
    if (needed <= 0) return "";
    std::string out((size_t)needed, '\0');
    WideCharToMultiByte(CP_UTF8, 0, (const wchar_t*)ts, len, &out[0], needed, nullptr, nullptr);
    return out;
}

static void send_startup_error(int ipc_out, const char* msg) {
    uint32_t status = 0;
    uint32_t len = msg ? (uint32_t)strlen(msg) : 0;
    write_exact(ipc_out, &status, 4);
    write_exact(ipc_out, &len, 4);
    if (len > 0) write_exact(ipc_out, msg, len);
}

/* -------------------------------------------------------------------------- */
/* VST3 Host COM Implementations                                              */
/* -------------------------------------------------------------------------- */

class MemoryStream final : public IBStream, public ISizeableStream {
public:
    std::vector<uint8_t> data;
    int64 pos = 0;
    std::atomic<int32> refCount{1};

    MemoryStream() = default;
    explicit MemoryStream(const uint8_t* ptr, size_t sz) : data(ptr, ptr + sz), pos(0) {}

    tresult PLUGIN_API queryInterface(const TUID _iid, void** obj) override {
        if (FUnknownPrivate::iidEqual(_iid, FUnknown::iid) ||
            FUnknownPrivate::iidEqual(_iid, IBStream::iid)) {
            addRef();
            *obj = static_cast<IBStream*>(this);
            return kResultOk;
        }
        if (FUnknownPrivate::iidEqual(_iid, ISizeableStream::iid)) {
            addRef();
            *obj = static_cast<ISizeableStream*>(this);
            return kResultOk;
        }
        *obj = nullptr;
        return kNoInterface;
    }

    uint32 PLUGIN_API addRef() override { return (uint32)++refCount; }
    uint32 PLUGIN_API release() override {
        int32 r = --refCount;
        if (r == 0) delete this;
        return (uint32)r;
    }

    tresult PLUGIN_API read(void* buffer, int32 numBytes, int32* numBytesRead) override {
        if (!buffer || numBytes < 0) return kInvalidArgument;
        int64 avail = (int64)data.size() - pos;
        if (avail <= 0) {
            if (numBytesRead) *numBytesRead = 0;
            return kResultOk;
        }
        int32 toRead = (int32)std::min<int64>(avail, (int64)numBytes);
        memcpy(buffer, data.data() + pos, (size_t)toRead);
        pos += toRead;
        if (numBytesRead) *numBytesRead = toRead;
        return kResultOk;
    }

    tresult PLUGIN_API write(void* buffer, int32 numBytes, int32* numBytesWritten) override {
        if (!buffer || numBytes < 0) return kInvalidArgument;
        if (pos < 0) pos = 0;
        size_t endPos = (size_t)pos + (size_t)numBytes;
        if (endPos > data.size()) data.resize(endPos);
        if (numBytes > 0) memcpy(data.data() + pos, buffer, (size_t)numBytes);
        pos += numBytes;
        if (numBytesWritten) *numBytesWritten = numBytes;
        return kResultOk;
    }

    tresult PLUGIN_API seek(int64 offset, int32 mode, int64* result) override {
        int64 newPos = pos;
        if (mode == kIBSeekSet) newPos = offset;
        else if (mode == kIBSeekCur) newPos = pos + offset;
        else if (mode == kIBSeekEnd) newPos = (int64)data.size() + offset;
        else return kInvalidArgument;
        if (newPos < 0) newPos = 0;
        pos = newPos;
        if (result) *result = pos;
        return kResultOk;
    }

    tresult PLUGIN_API tell(int64* p) override {
        if (!p) return kInvalidArgument;
        *p = pos;
        return kResultOk;
    }

    tresult PLUGIN_API getStreamSize(int64& size) override {
        size = (int64)data.size();
        return kResultOk;
    }

    tresult PLUGIN_API setStreamSize(int64 size) override {
        if (size < 0) return kInvalidArgument;
        data.resize((size_t)size);
        if (pos > size) pos = size;
        return kResultOk;
    }
};

class HostAttributeList final : public IAttributeList {
public:
    struct Entry {
        enum Kind { kInt, kFloat, kStr, kBin } kind = kInt;
        int64 i = 0;
        double f = 0.0;
        std::vector<char16> s;
        std::vector<uint8_t> b;
    };
    std::map<std::string, Entry> entries;
    std::atomic<int32> refCount{1};

    tresult PLUGIN_API queryInterface(const TUID _iid, void** obj) override {
        if (FUnknownPrivate::iidEqual(_iid, FUnknown::iid) ||
            FUnknownPrivate::iidEqual(_iid, IAttributeList::iid)) {
            addRef();
            *obj = static_cast<IAttributeList*>(this);
            return kResultOk;
        }
        *obj = nullptr;
        return kNoInterface;
    }
    uint32 PLUGIN_API addRef() override { return (uint32)++refCount; }
    uint32 PLUGIN_API release() override {
        int32 r = --refCount;
        if (r == 0) delete this;
        return (uint32)r;
    }

    tresult PLUGIN_API setInt(AttrID id, int64 value) override {
        if (!id) return kInvalidArgument;
        Entry e; e.kind = Entry::kInt; e.i = value;
        entries[id] = std::move(e);
        return kResultOk;
    }
    tresult PLUGIN_API getInt(AttrID id, int64& value) override {
        if (!id) return kInvalidArgument;
        auto it = entries.find(id);
        if (it == entries.end() || it->second.kind != Entry::kInt) return kResultFalse;
        value = it->second.i;
        return kResultOk;
    }
    tresult PLUGIN_API setFloat(AttrID id, double value) override {
        if (!id) return kInvalidArgument;
        Entry e; e.kind = Entry::kFloat; e.f = value;
        entries[id] = std::move(e);
        return kResultOk;
    }
    tresult PLUGIN_API getFloat(AttrID id, double& value) override {
        if (!id) return kInvalidArgument;
        auto it = entries.find(id);
        if (it == entries.end() || it->second.kind != Entry::kFloat) return kResultFalse;
        value = it->second.f;
        return kResultOk;
    }
    tresult PLUGIN_API setString(AttrID id, const TChar* string) override {
        if (!id || !string) return kInvalidArgument;
        Entry e; e.kind = Entry::kStr;
        const TChar* p = string;
        while (*p) { e.s.push_back(*p); ++p; }
        e.s.push_back(0);
        entries[id] = std::move(e);
        return kResultOk;
    }
    tresult PLUGIN_API getString(AttrID id, TChar* string, uint32 sizeInBytes) override {
        if (!id || !string || sizeInBytes < sizeof(TChar)) return kInvalidArgument;
        auto it = entries.find(id);
        if (it == entries.end() || it->second.kind != Entry::kStr) return kResultFalse;
        size_t maxChars = sizeInBytes / sizeof(TChar);
        size_t copyChars = std::min(maxChars - 1, it->second.s.size() > 0 ? it->second.s.size() - 1 : 0);
        for (size_t i = 0; i < copyChars; ++i) string[i] = it->second.s[i];
        string[copyChars] = 0;
        return kResultOk;
    }
    tresult PLUGIN_API setBinary(AttrID id, const void* data, uint32 sizeInBytes) override {
        if (!id) return kInvalidArgument;
        Entry e; e.kind = Entry::kBin;
        if (data && sizeInBytes > 0) {
            const uint8_t* bp = (const uint8_t*)data;
            e.b.assign(bp, bp + sizeInBytes);
        }
        entries[id] = std::move(e);
        return kResultOk;
    }
    tresult PLUGIN_API getBinary(AttrID id, const void*& data, uint32& sizeInBytes) override {
        if (!id) return kInvalidArgument;
        auto it = entries.find(id);
        if (it == entries.end() || it->second.kind != Entry::kBin) return kResultFalse;
        data = it->second.b.empty() ? nullptr : it->second.b.data();
        sizeInBytes = (uint32)it->second.b.size();
        return kResultOk;
    }
};

class HostMessage final : public IMessage {
public:
    std::string msgId;
    HostAttributeList* attrs = new HostAttributeList();
    std::atomic<int32> refCount{1};

    ~HostMessage() {
        if (attrs) attrs->release();
    }

    tresult PLUGIN_API queryInterface(const TUID _iid, void** obj) override {
        if (FUnknownPrivate::iidEqual(_iid, FUnknown::iid) ||
            FUnknownPrivate::iidEqual(_iid, IMessage::iid)) {
            addRef();
            *obj = static_cast<IMessage*>(this);
            return kResultOk;
        }
        *obj = nullptr;
        return kNoInterface;
    }
    uint32 PLUGIN_API addRef() override { return (uint32)++refCount; }
    uint32 PLUGIN_API release() override {
        int32 r = --refCount;
        if (r == 0) delete this;
        return (uint32)r;
    }

    FIDString PLUGIN_API getMessageID() override { return msgId.c_str(); }
    void PLUGIN_API setMessageID(FIDString id) override { msgId = id ? id : ""; }
    IAttributeList* PLUGIN_API getAttributes() override { return attrs; }
};

class HostApplication final : public IHostApplication, public IPlugInterfaceSupport {
public:
    tresult PLUGIN_API queryInterface(const TUID _iid, void** obj) override {
        if (FUnknownPrivate::iidEqual(_iid, FUnknown::iid) ||
            FUnknownPrivate::iidEqual(_iid, IHostApplication::iid)) {
            addRef();
            *obj = static_cast<IHostApplication*>(this);
            return kResultOk;
        }
        if (FUnknownPrivate::iidEqual(_iid, IPlugInterfaceSupport::iid)) {
            addRef();
            *obj = static_cast<IPlugInterfaceSupport*>(this);
            return kResultOk;
        }
        *obj = nullptr;
        return kNoInterface;
    }
    uint32 PLUGIN_API addRef() override { return 1; }
    uint32 PLUGIN_API release() override { return 1; }

    tresult PLUGIN_API getName(String128 name) override {
        const char16_t* src = u"NovaDAW";
        int i = 0;
        while (src[i] && i < 127) {
            name[i] = src[i];
            ++i;
        }
        name[i] = 0;
        return kResultOk;
    }

    tresult PLUGIN_API createInstance(TUID cid, TUID _iid, void** obj) override {
        if (!obj) return kInvalidArgument;
        *obj = nullptr;
        if (FUnknownPrivate::iidEqual(cid, IMessage::iid) &&
            FUnknownPrivate::iidEqual(_iid, IMessage::iid)) {
            *obj = static_cast<IMessage*>(new HostMessage());
            return kResultOk;
        }
        if (FUnknownPrivate::iidEqual(cid, IAttributeList::iid) &&
            FUnknownPrivate::iidEqual(_iid, IAttributeList::iid)) {
            *obj = static_cast<IAttributeList*>(new HostAttributeList());
            return kResultOk;
        }
        return kNoInterface;
    }

    tresult PLUGIN_API isPlugInterfaceSupported(const TUID _iid) override {
        if (FUnknownPrivate::iidEqual(_iid, IComponent::iid) ||
            FUnknownPrivate::iidEqual(_iid, IAudioProcessor::iid) ||
            FUnknownPrivate::iidEqual(_iid, IEditController::iid) ||
            FUnknownPrivate::iidEqual(_iid, IConnectionPoint::iid) ||
            FUnknownPrivate::iidEqual(_iid, IMidiMapping::iid) ||
            FUnknownPrivate::iidEqual(_iid, IPlugView::iid)) {
            return kResultTrue;
        }
        return kResultFalse;
    }
};

class HostEventList final : public IEventList {
public:
    std::vector<Event> events;

    tresult PLUGIN_API queryInterface(const TUID _iid, void** obj) override {
        if (FUnknownPrivate::iidEqual(_iid, FUnknown::iid) ||
            FUnknownPrivate::iidEqual(_iid, IEventList::iid)) {
            *obj = static_cast<IEventList*>(this);
            return kResultOk;
        }
        *obj = nullptr;
        return kNoInterface;
    }
    uint32 PLUGIN_API addRef() override { return 1; }
    uint32 PLUGIN_API release() override { return 1; }

    int32 PLUGIN_API getEventCount() override { return (int32)events.size(); }
    tresult PLUGIN_API getEvent(int32 index, Event& e) override {
        if (index < 0 || index >= (int32)events.size()) return kInvalidArgument;
        e = events[(size_t)index];
        return kResultOk;
    }
    tresult PLUGIN_API addEvent(Event& e) override {
        events.push_back(e);
        return kResultOk;
    }
};

class HostParamValueQueue final : public IParamValueQueue {
public:
    ParamID paramId = 0;
    struct Pt { int32 offset; ParamValue value; };
    std::vector<Pt> points;

    tresult PLUGIN_API queryInterface(const TUID _iid, void** obj) override {
        if (FUnknownPrivate::iidEqual(_iid, FUnknown::iid) ||
            FUnknownPrivate::iidEqual(_iid, IParamValueQueue::iid)) {
            *obj = static_cast<IParamValueQueue*>(this);
            return kResultOk;
        }
        *obj = nullptr;
        return kNoInterface;
    }
    uint32 PLUGIN_API addRef() override { return 1; }
    uint32 PLUGIN_API release() override { return 1; }

    ParamID PLUGIN_API getParameterId() override { return paramId; }
    int32 PLUGIN_API getPointCount() override { return (int32)points.size(); }
    tresult PLUGIN_API getPoint(int32 index, int32& sampleOffset, ParamValue& value) override {
        if (index < 0 || index >= (int32)points.size()) return kInvalidArgument;
        sampleOffset = points[(size_t)index].offset;
        value = points[(size_t)index].value;
        return kResultOk;
    }
    tresult PLUGIN_API addPoint(int32 sampleOffset, ParamValue value, int32& index) override {
        index = (int32)points.size();
        points.push_back({sampleOffset, value});
        return kResultOk;
    }
};

class HostParameterChanges final : public IParameterChanges {
public:
    std::vector<HostParamValueQueue> queues;

    void clear() { queues.clear(); }

    void addChange(ParamID id, int32 sampleOffset, ParamValue value) {
        for (auto& q : queues) {
            if (q.paramId == id) {
                int32 idx = 0;
                q.addPoint(sampleOffset, value, idx);
                return;
            }
        }
        HostParamValueQueue nq;
        nq.paramId = id;
        int32 idx = 0;
        nq.addPoint(sampleOffset, value, idx);
        queues.push_back(std::move(nq));
    }

    tresult PLUGIN_API queryInterface(const TUID _iid, void** obj) override {
        if (FUnknownPrivate::iidEqual(_iid, FUnknown::iid) ||
            FUnknownPrivate::iidEqual(_iid, IParameterChanges::iid)) {
            *obj = static_cast<IParameterChanges*>(this);
            return kResultOk;
        }
        *obj = nullptr;
        return kNoInterface;
    }
    uint32 PLUGIN_API addRef() override { return 1; }
    uint32 PLUGIN_API release() override { return 1; }

    int32 PLUGIN_API getParameterCount() override { return (int32)queues.size(); }
    IParamValueQueue* PLUGIN_API getParameterData(int32 index) override {
        if (index < 0 || index >= (int32)queues.size()) return nullptr;
        return &queues[(size_t)index];
    }
    IParamValueQueue* PLUGIN_API addParameterData(const ParamID& id, int32& index) override {
        for (size_t i = 0; i < queues.size(); ++i) {
            if (queues[i].paramId == id) {
                index = (int32)i;
                return &queues[i];
            }
        }
        index = (int32)queues.size();
        HostParamValueQueue nq;
        nq.paramId = id;
        queues.push_back(std::move(nq));
        return &queues.back();
    }
};

/* -------------------------------------------------------------------------- */
/* Global Bridge State                                                        */
/* -------------------------------------------------------------------------- */

static HostApplication g_hostApp;
static IPluginFactory* g_factory = nullptr;
static IComponent* g_comp = nullptr;
static IAudioProcessor* g_proc = nullptr;
static IEditController* g_ctrl = nullptr;
static IConnectionPoint* g_compCP = nullptr;
static IConnectionPoint* g_ctrlCP = nullptr;
static IMidiMapping* g_midiMap = nullptr;
static bool g_ctrlIsSeparate = false;

static double g_sampleRate = 44100.0;
static int32 g_maxBlockSize = 16384;
static int32 g_numAudioInBuses = 0;
static int32 g_numAudioOutBuses = 0;
static std::vector<int32> g_inBusChannels;
static std::vector<int32> g_outBusChannels;
static std::vector<std::vector<float>> g_scratchBusData;
static int64 g_projectSamples = 0;

static std::mutex g_paramMutex;
static std::vector<std::pair<ParamID, ParamValue>> g_pendingUiParams;
static std::atomic<bool> g_needsRestartLatency{false};

static HWND g_editorHwnd = nullptr;
static IPlugView* g_plugView = nullptr;
static const wchar_t* EDITOR_WND_CLASS = L"NovaDAWVST3Bridge64Wnd";
static DWORD g_editorStyle = WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX;

static void resize_editor_window(int w, int h) {
    if (!g_editorHwnd || w <= 0 || h <= 0) return;
    RECT rc = { 0, 0, w, h };
    AdjustWindowRectEx(&rc, g_editorStyle, FALSE, 0);
    SetWindowPos(g_editorHwnd, nullptr, 0, 0, rc.right - rc.left, rc.bottom - rc.top,
                 SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE);
}

class HostComponentHandler final : public IComponentHandler, public IComponentHandler2 {
public:
    tresult PLUGIN_API queryInterface(const TUID _iid, void** obj) override {
        if (FUnknownPrivate::iidEqual(_iid, FUnknown::iid) ||
            FUnknownPrivate::iidEqual(_iid, IComponentHandler::iid)) {
            *obj = static_cast<IComponentHandler*>(this);
            return kResultOk;
        }
        if (FUnknownPrivate::iidEqual(_iid, IComponentHandler2::iid)) {
            *obj = static_cast<IComponentHandler2*>(this);
            return kResultOk;
        }
        *obj = nullptr;
        return kNoInterface;
    }
    uint32 PLUGIN_API addRef() override { return 1; }
    uint32 PLUGIN_API release() override { return 1; }

    tresult PLUGIN_API beginEdit(ParamID) override { return kResultOk; }
    tresult PLUGIN_API performEdit(ParamID id, ParamValue valueNormalized) override {
        std::lock_guard<std::mutex> lock(g_paramMutex);
        g_pendingUiParams.emplace_back(id, valueNormalized);
        return kResultOk;
    }
    tresult PLUGIN_API endEdit(ParamID) override { return kResultOk; }
    tresult PLUGIN_API restartComponent(int32 flags) override {
        if (flags & (kIoChanged | kLatencyChanged)) {
            g_needsRestartLatency = true;
        }
        return kResultOk;
    }

    tresult PLUGIN_API setDirty(TBool) override { return kResultOk; }
    tresult PLUGIN_API requestOpenEditor(FIDString) override { return kResultOk; }
    tresult PLUGIN_API startGroupEdit() override { return kResultOk; }
    tresult PLUGIN_API finishGroupEdit() override { return kResultOk; }
};

class HostPlugFrame final : public IPlugFrame {
public:
    tresult PLUGIN_API queryInterface(const TUID _iid, void** obj) override {
        if (FUnknownPrivate::iidEqual(_iid, FUnknown::iid) ||
            FUnknownPrivate::iidEqual(_iid, IPlugFrame::iid)) {
            *obj = static_cast<IPlugFrame*>(this);
            return kResultOk;
        }
        *obj = nullptr;
        return kNoInterface;
    }
    uint32 PLUGIN_API addRef() override { return 1; }
    uint32 PLUGIN_API release() override { return 1; }

    tresult PLUGIN_API resizeView(IPlugView* view, ViewRect* newSize) override {
        if (!view || !newSize) return kInvalidArgument;
        int w = newSize->getWidth();
        int h = newSize->getHeight();
        if (w > 0 && h > 0) {
            resize_editor_window(w, h);
        }
        view->onSize(newSize);
        return kResultOk;
    }
};

static HostComponentHandler g_compHandler;
static HostPlugFrame g_plugFrame;

static LRESULT CALLBACK editor_wnd_proc(HWND hwnd, UINT msg, WPARAM wParam, LPARAM lParam) {
    if (msg == WM_CLOSE) {
        if (g_plugView) {
            g_plugView->removed();
            g_plugView->setFrame(nullptr);
            g_plugView->release();
            g_plugView = nullptr;
        }
        DestroyWindow(hwnd);
        g_editorHwnd = nullptr;
        return 0;
    }
    if (msg == WM_DESTROY) {
        g_editorHwnd = nullptr;
        return 0;
    }
    return DefWindowProcW(hwnd, msg, wParam, lParam);
}

static void close_editor_internal() {
    if (g_plugView) {
        g_plugView->removed();
        g_plugView->setFrame(nullptr);
        g_plugView->release();
        g_plugView = nullptr;
    }
    if (g_editorHwnd) {
        HWND h = g_editorHwnd;
        g_editorHwnd = nullptr;
        DestroyWindow(h);
    }
}

/* -------------------------------------------------------------------------- */
/* Helpers for Bus Setup, Processing, State, and Bundle Resolution            */
/* -------------------------------------------------------------------------- */

static std::wstring resolve_vst3_binary(const std::wstring& inputPath) {
    DWORD attrs = GetFileAttributesW(inputPath.c_str());
    if (attrs != INVALID_FILE_ATTRIBUTES && (attrs & FILE_ATTRIBUTE_DIRECTORY)) {
        std::wstring archDir = inputPath + L"\\Contents\\x86_64-win";
        // Extract bundle stem: Foo.vst3 -> Foo.vst3 inside x86_64-win
        size_t slash = inputPath.find_last_of(L"\\/");
        std::wstring base = (slash == std::wstring::npos) ? inputPath : inputPath.substr(slash + 1);
        std::wstring exact = archDir + L"\\" + base;
        if (GetFileAttributesW(exact.c_str()) != INVALID_FILE_ATTRIBUTES) {
            return exact;
        }
        WIN32_FIND_DATAW fd;
        HANDLE hFind = FindFirstFileW((archDir + L"\\*.vst3").c_str(), &fd);
        if (hFind != INVALID_HANDLE_VALUE) {
            std::wstring first = archDir + L"\\" + fd.cFileName;
            FindClose(hFind);
            return first;
        }
    }
    return inputPath;
}

static void refresh_bus_topology() {
    g_numAudioInBuses = g_comp->getBusCount(kAudio, kInput);
    g_numAudioOutBuses = g_comp->getBusCount(kAudio, kOutput);
    g_inBusChannels.assign((size_t)g_numAudioInBuses, 0);
    g_outBusChannels.assign((size_t)g_numAudioOutBuses, 0);

    for (int32 i = 0; i < g_numAudioInBuses; ++i) {
        BusInfo bi{};
        if (g_comp->getBusInfo(kAudio, kInput, i, bi) == kResultOk) {
            g_inBusChannels[(size_t)i] = bi.channelCount;
        }
    }
    for (int32 i = 0; i < g_numAudioOutBuses; ++i) {
        BusInfo bi{};
        if (g_comp->getBusInfo(kAudio, kOutput, i, bi) == kResultOk) {
            g_outBusChannels[(size_t)i] = bi.channelCount;
        }
    }
}

static void configure_and_start_processing(double sampleRate) {
    g_sampleRate = (sampleRate > 0.0) ? sampleRate : 44100.0;

    g_proc->setProcessing(false);
    g_comp->setActive(false);

    // Try setting main input and main output to stereo
    int32 inCount = g_comp->getBusCount(kAudio, kInput);
    int32 outCount = g_comp->getBusCount(kAudio, kOutput);
    std::vector<SpeakerArrangement> inArr((size_t)inCount, SpeakerArr::kStereo);
    std::vector<SpeakerArrangement> outArr((size_t)outCount, SpeakerArr::kStereo);
    for (int32 i = 0; i < inCount; ++i) {
        SpeakerArrangement cur = SpeakerArr::kStereo;
        if (g_proc->getBusArrangement(kInput, i, cur) == kResultOk && i > 0) {
            inArr[(size_t)i] = cur;
        }
    }
    for (int32 i = 0; i < outCount; ++i) {
        SpeakerArrangement cur = SpeakerArr::kStereo;
        if (g_proc->getBusArrangement(kOutput, i, cur) == kResultOk && i > 0) {
            outArr[(size_t)i] = cur;
        }
    }
    if (inCount > 0 || outCount > 0) {
        g_proc->setBusArrangements(
            inCount > 0 ? inArr.data() : nullptr, inCount,
            outCount > 0 ? outArr.data() : nullptr, outCount
        );
    }

    for (int32 i = 0; i < inCount; ++i) {
        g_comp->activateBus(kAudio, kInput, i, i == 0);
    }
    for (int32 i = 0; i < outCount; ++i) {
        g_comp->activateBus(kAudio, kOutput, i, i == 0);
    }
    int32 evInCount = g_comp->getBusCount(kEvent, kInput);
    for (int32 i = 0; i < evInCount; ++i) {
        g_comp->activateBus(kEvent, kInput, i, i == 0);
    }

    refresh_bus_topology();

    ProcessSetup setup{};
    setup.processMode = kRealtime;
    setup.symbolicSampleSize = kSample32;
    setup.maxSamplesPerBlock = g_maxBlockSize;
    setup.sampleRate = g_sampleRate;
    g_proc->setupProcessing(setup);

    g_comp->setActive(true);
    g_proc->setProcessing(true);
}

static std::vector<uint8_t> save_full_state() {
    MemoryStream* compStream = new MemoryStream();
    g_comp->getState(compStream);

    MemoryStream* ctrlStream = new MemoryStream();
    if (g_ctrl) {
        g_ctrl->getState(ctrlStream);
    }

    // Format: "NVST3\0\x01\x00" (8 bytes) + uint32 compLen + compBytes + uint32 ctrlLen + ctrlBytes
    uint32_t compLen = (uint32_t)compStream->data.size();
    uint32_t ctrlLen = (uint32_t)ctrlStream->data.size();
    std::vector<uint8_t> blob;
    blob.reserve(16 + compLen + ctrlLen);
    const uint8_t magic[8] = {'N', 'V', 'S', 'T', '3', 0, 1, 0};
    blob.insert(blob.end(), magic, magic + 8);
    blob.insert(blob.end(), (uint8_t*)&compLen, (uint8_t*)&compLen + 4);
    blob.insert(blob.end(), compStream->data.begin(), compStream->data.end());
    blob.insert(blob.end(), (uint8_t*)&ctrlLen, (uint8_t*)&ctrlLen + 4);
    blob.insert(blob.end(), ctrlStream->data.begin(), ctrlStream->data.end());

    compStream->release();
    ctrlStream->release();
    return blob;
}

static bool restore_full_state(const uint8_t* data, size_t sz) {
    if (!data || sz == 0) return true;
    const uint8_t magic[8] = {'N', 'V', 'S', 'T', '3', 0, 1, 0};
    if (sz >= 16 && memcmp(data, magic, 8) == 0) {
        uint32_t compLen = 0, ctrlLen = 0;
        memcpy(&compLen, data + 8, 4);
        size_t off = 12;
        if (off + compLen + 4 <= sz) {
            memcpy(&ctrlLen, data + off + compLen, 4);
            if (off + compLen + 4 + ctrlLen <= sz) {
                if (compLen > 0) {
                    MemoryStream* compStream = new MemoryStream(data + off, compLen);
                    g_comp->setState(compStream);
                    if (g_ctrl) {
                        compStream->seek(0, IBStream::kIBSeekSet, nullptr);
                        g_ctrl->setComponentState(compStream);
                    }
                    compStream->release();
                }
                if (ctrlLen > 0 && g_ctrl) {
                    MemoryStream* ctrlStream = new MemoryStream(data + off + compLen + 4, ctrlLen);
                    g_ctrl->setState(ctrlStream);
                    ctrlStream->release();
                }
                return true;
            }
        }
    }

    // Fallback: raw component stream (e.g. legacy state chunk)
    MemoryStream* rawStream = new MemoryStream(data, sz);
    tresult res = g_comp->setState(rawStream);
    if (g_ctrl) {
        rawStream->seek(0, IBStream::kIBSeekSet, nullptr);
        g_ctrl->setComponentState(rawStream);
    }
    rawStream->release();
    return res == kResultOk || res == kResultTrue;
}

static HANDLE g_hCmdReady = nullptr;
static HANDLE g_hNextCmdAllowed = nullptr;
static int g_ipcInFd = -1;
static uint8_t g_pendingCmd = 0;
static std::atomic<bool> g_stdinClosed{false};

static DWORD WINAPI stdin_watcher_thread(LPVOID) {
    while (true) {
        uint8_t cmd = 0;
        if (!read_exact(g_ipcInFd, &cmd, 1)) {
            g_stdinClosed = true;
            SetEvent(g_hCmdReady);
            return 0;
        }
        g_pendingCmd = cmd;
        SetEvent(g_hCmdReady);
        WaitForSingleObject(g_hNextCmdAllowed, INFINITE);
    }
    return 0;
}

/* -------------------------------------------------------------------------- */
/* Main Entry Point                                                           */
/* -------------------------------------------------------------------------- */

int wmain(int argc, wchar_t* argv[]) {
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX);
    CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);

    _setmode(_fileno(stdin), _O_BINARY);
    _setmode(_fileno(stdout), _O_BINARY);

    int ipc_in = _fileno(stdin);
    int ipc_out = _dup(_fileno(stdout));
    _setmode(ipc_out, _O_BINARY);
    _dup2(_fileno(stderr), _fileno(stdout));

    if (argc < 2) {
        send_startup_error(ipc_out, "Missing VST3 path argument");
        return 1;
    }

    bool probe_only = false;
    std::string wanted_sub_name;
    for (int i = 2; i < argc; ++i) {
        if (wcscmp(argv[i], L"--probe") == 0) {
            probe_only = true;
        } else if (wcscmp(argv[i], L"--sub") == 0 && i + 1 < argc) {
            wanted_sub_name = wide_to_utf8(argv[++i]);
        }
    }

    std::wstring dllPath = resolve_vst3_binary(argv[1]);

    // Add DLL directory to DLL search path for companion libraries
    size_t slash = dllPath.find_last_of(L"\\/");
    if (slash != std::wstring::npos) {
        std::wstring dir = dllPath.substr(0, slash);
        SetDllDirectoryW(dir.c_str());
    }

    HMODULE hMod = LoadLibraryW(dllPath.c_str());
    if (!hMod) {
        char err[256];
        snprintf(err, sizeof(err), "LoadLibraryW failed for VST3 (Win32 error %lu)", GetLastError());
        send_startup_error(ipc_out, err);
        return 1;
    }

    typedef bool (*InitDllProc)();
    typedef bool (*ExitDllProc)();
    typedef IPluginFactory* (PLUGIN_API *GetFactoryProc)();

    InitDllProc initDll = (InitDllProc)GetProcAddress(hMod, "InitDll");
    ExitDllProc exitDll = (ExitDllProc)GetProcAddress(hMod, "ExitDll");
    if (initDll) {
        initDll();
    }

    GetFactoryProc getFactory = (GetFactoryProc)GetProcAddress(hMod, "GetPluginFactory");
    if (!getFactory) {
        send_startup_error(ipc_out, "No GetPluginFactory export found in VST3 binary");
        if (exitDll) exitDll();
        return 1;
    }

    g_factory = getFactory();
    if (!g_factory) {
        send_startup_error(ipc_out, "GetPluginFactory returned null");
        if (exitDll) exitDll();
        return 1;
    }

    IPluginFactory2* factory2 = nullptr;
    g_factory->queryInterface(IPluginFactory2::iid, (void**)&factory2);

    TUID chosenCid{};
    bool foundClass = false;
    std::string pluginName;
    std::string subCategories;

    int32 nClasses = g_factory->countClasses();
    for (int32 i = 0; i < nClasses; ++i) {
        PClassInfo ci{};
        if (g_factory->getClassInfo(i, &ci) != kResultOk) continue;
        if (strcmp(ci.category, kVstAudioEffectClass) != 0) continue;

        std::string cname(ci.name);
        std::string subcats;
        if (factory2) {
            PClassInfo2 ci2{};
            if (factory2->getClassInfo2(i, &ci2) == kResultOk) {
                subcats = ci2.subCategories;
            }
        }

        if (!wanted_sub_name.empty()) {
            if (cname == wanted_sub_name) {
                memcpy(chosenCid, ci.cid, sizeof(TUID));
                pluginName = cname;
                subCategories = subcats;
                foundClass = true;
                break;
            }
        } else if (!foundClass) {
            memcpy(chosenCid, ci.cid, sizeof(TUID));
            pluginName = cname;
            subCategories = subcats;
            foundClass = true;
            break;
        }
    }
    if (factory2) factory2->release();

    if (!foundClass) {
        send_startup_error(ipc_out, "No matching kVstAudioEffectClass found in VST3 factory");
        g_factory->release();
        if (exitDll) exitDll();
        return 1;
    }

    if (g_factory->createInstance(chosenCid, IComponent::iid, (void**)&g_comp) != kResultOk || !g_comp) {
        send_startup_error(ipc_out, "Failed to create VST3 IComponent instance");
        g_factory->release();
        if (exitDll) exitDll();
        return 1;
    }

    if (g_comp->initialize(static_cast<IHostApplication*>(&g_hostApp)) != kResultOk) {
        send_startup_error(ipc_out, "IComponent::initialize failed");
        g_comp->release();
        g_factory->release();
        if (exitDll) exitDll();
        return 1;
    }

    if (g_comp->queryInterface(IAudioProcessor::iid, (void**)&g_proc) != kResultOk || !g_proc) {
        send_startup_error(ipc_out, "Plugin does not implement IAudioProcessor");
        g_comp->terminate();
        g_comp->release();
        g_factory->release();
        if (exitDll) exitDll();
        return 1;
    }

    // Obtain EditController (single-component or separate class)
    if (g_comp->queryInterface(IEditController::iid, (void**)&g_ctrl) != kResultOk || !g_ctrl) {
        TUID ctrlCid{};
        if (g_comp->getControllerClassId(ctrlCid) == kResultOk) {
            if (g_factory->createInstance(ctrlCid, IEditController::iid, (void**)&g_ctrl) == kResultOk && g_ctrl) {
                g_ctrlIsSeparate = true;
                if (g_ctrl->initialize(static_cast<IHostApplication*>(&g_hostApp)) != kResultOk) {
                    g_ctrl->release();
                    g_ctrl = nullptr;
                    g_ctrlIsSeparate = false;
                }
            }
        }
    }

    if (g_ctrl) {
        g_ctrl->setComponentHandler(static_cast<IComponentHandler*>(&g_compHandler));
        g_ctrl->queryInterface(IMidiMapping::iid, (void**)&g_midiMap);
        if (g_ctrlIsSeparate) {
            g_comp->queryInterface(IConnectionPoint::iid, (void**)&g_compCP);
            g_ctrl->queryInterface(IConnectionPoint::iid, (void**)&g_ctrlCP);
            if (g_compCP && g_ctrlCP) {
                g_compCP->connect(g_ctrlCP);
                g_ctrlCP->connect(g_compCP);
            }
            MemoryStream* initState = new MemoryStream();
            if (g_comp->getState(initState) == kResultOk && !initState->data.empty()) {
                initState->seek(0, IBStream::kIBSeekSet, nullptr);
                g_ctrl->setComponentState(initState);
            }
            initState->release();
        }
    }

    configure_and_start_processing(44100.0);
    if (!probe_only) {
        MSG msg;
        while (PeekMessageW(&msg, nullptr, 0, 0, PM_REMOVE)) {
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
    }

    int32 evInCount = g_comp->getBusCount(kEvent, kInput);
    int32 mainAudioInCh = (!g_inBusChannels.empty()) ? g_inBusChannels[0] : 0;
    int32 mainAudioOutCh = (!g_outBusChannels.empty()) ? g_outBusChannels[0] : 2;
    bool is_synth = (subCategories.find("Instrument") != std::string::npos) ||
                    (mainAudioInCh == 0 && evInCount > 0) ||
                    (g_numAudioInBuses == 0 && evInCount > 0);
    int32 num_params = g_ctrl ? g_ctrl->getParameterCount() : 0;

    uint32_t status = 1;
    int32_t synth_i = is_synth ? 1 : 0;
    int32_t in_i = mainAudioInCh;
    int32_t out_i = std::max<int32>(2, mainAudioOutCh);
    int32_t param_i = num_params;
    uint32_t name_len = (uint32_t)pluginName.size();

    write_exact(ipc_out, &status, 4);
    write_exact(ipc_out, &synth_i, 4);
    write_exact(ipc_out, &in_i, 4);
    write_exact(ipc_out, &out_i, 4);
    write_exact(ipc_out, &param_i, 4);
    write_exact(ipc_out, &name_len, 4);
    if (name_len > 0) write_exact(ipc_out, pluginName.data(), name_len);

    if (probe_only) {
        g_proc->setProcessing(false);
        g_comp->setActive(false);
        if (g_compCP && g_ctrlCP) { g_compCP->disconnect(g_ctrlCP); g_ctrlCP->disconnect(g_compCP); }
        if (g_compCP) g_compCP->release();
        if (g_ctrlCP) g_ctrlCP->release();
        if (g_midiMap) g_midiMap->release();
        if (g_ctrl) { if (g_ctrlIsSeparate) g_ctrl->terminate(); g_ctrl->release(); }
        g_proc->release();
        g_comp->terminate();
        g_comp->release();
        g_factory->release();
        if (exitDll) exitDll();
        ExitProcess(0);
    }

    WNDCLASSEXW wc{};
    wc.cbSize = sizeof(WNDCLASSEXW);
    wc.style = CS_HREDRAW | CS_VREDRAW;
    wc.lpfnWndProc = editor_wnd_proc;
    wc.hInstance = GetModuleHandleW(nullptr);
    wc.hCursor = LoadCursorW(nullptr, MAKEINTRESOURCEW(32512));
    wc.hbrBackground = (HBRUSH)(COLOR_WINDOW + 1);
    wc.lpszClassName = EDITOR_WND_CLASS;
    RegisterClassExW(&wc);

    g_ipcInFd = ipc_in;
    g_hCmdReady = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    g_hNextCmdAllowed = CreateEventW(nullptr, FALSE, FALSE, nullptr);
    HANDLE hWatcher = CreateThread(nullptr, 0, stdin_watcher_thread, nullptr, 0, nullptr);
    if (hWatcher) CloseHandle(hWatcher);

    HostEventList inEvents;
    HostParameterChanges inParams;
    std::vector<float> zeroBuf((size_t)g_maxBlockSize, 0.0f);

    while (true) {
        MSG msg;
        while (PeekMessageW(&msg, nullptr, 0, 0, PM_REMOVE)) {
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }

        if (g_editorHwnd && !IsWindow(g_editorHwnd)) {
            close_editor_internal();
        }

        DWORD waitRes = MsgWaitForMultipleObjectsEx(
            1, &g_hCmdReady, INFINITE, QS_ALLINPUT, MWMO_INPUTAVAILABLE
        );
        if (waitRes == WAIT_OBJECT_0 + 1) {
            continue;
        }
        if (waitRes != WAIT_OBJECT_0) {
            break;
        }
        if (g_stdinClosed.load()) {
            break;
        }

        uint8_t cmd = g_pendingCmd;

        if (cmd == 'R') {
            float sr = 44100.0f;
            int32_t req_ch = 2;
            int32_t total_frames = 0;
            int32_t has_input = 0;
            int32_t num_midi = 0;
            int32_t reset_flag = 0;

            if (!read_exact(ipc_in, &sr, 4) ||
                !read_exact(ipc_in, &req_ch, 4) ||
                !read_exact(ipc_in, &total_frames, 4) ||
                !read_exact(ipc_in, &has_input, 4) ||
                !read_exact(ipc_in, &num_midi, 4) ||
                !read_exact(ipc_in, &reset_flag, 4)) {
                break;
            }

            if ((sr > 0.0f && (double)sr != g_sampleRate) || g_needsRestartLatency.exchange(false)) {
                configure_and_start_processing((sr > 0.0f) ? (double)sr : g_sampleRate);
            } else if (reset_flag) {
                g_proc->setProcessing(false);
                g_proc->setProcessing(true);
            }

            std::vector<MidiMsgRecord> midiRecs;
            if (num_midi > 0) {
                midiRecs.resize((size_t)num_midi);
                if (!read_exact(ipc_in, midiRecs.data(), sizeof(MidiMsgRecord) * (size_t)num_midi)) {
                    break;
                }
            }

            int32_t out_ch = req_ch > 0 ? req_ch : 2;
            std::vector<float> inPlanar;
            if (has_input && total_frames > 0) {
                inPlanar.resize((size_t)out_ch * (size_t)total_frames);
                if (!read_exact(ipc_in, inPlanar.data(), sizeof(float) * (size_t)out_ch * (size_t)total_frames)) {
                    break;
                }
            }

            std::vector<float> outPlanar((size_t)out_ch * (size_t)std::max(0, total_frames), 0.0f);

            // Process in chunks of up to g_maxBlockSize
            const int32 blockCap = g_maxBlockSize;
            if ((int32)zeroBuf.size() < blockCap) zeroBuf.assign((size_t)blockCap, 0.0f);

            // Prepare scratch buffers for secondary channels/buses
            int32 maxChNeeded = 0;
            for (int32 c : g_inBusChannels) maxChNeeded = std::max(maxChNeeded, c);
            for (int32 c : g_outBusChannels) maxChNeeded = std::max(maxChNeeded, c);
            std::vector<float> auxScratch((size_t)std::max(2, maxChNeeded) * (size_t)blockCap, 0.0f);

            size_t midiCursor = 0;
            int32 pos = 0;

            while (pos < total_frames) {
                int32 block = std::min(blockCap, total_frames - pos);
                inEvents.events.clear();
                inParams.clear();

                // Drain UI controller edits queued from editor window
                {
                    std::lock_guard<std::mutex> lock(g_paramMutex);
                    for (const auto& kv : g_pendingUiParams) {
                        inParams.addChange(kv.first, 0, kv.second);
                    }
                    g_pendingUiParams.clear();
                }

                // Populate MIDI events & CC/PitchBend parameter changes for this block
                while (midiCursor < midiRecs.size() && midiRecs[midiCursor].sample_idx < pos + block) {
                    const MidiMsgRecord& mr = midiRecs[midiCursor];
                    int32 delta = std::max<int32>(0, std::min<int32>(block - 1, mr.sample_idx - pos));
                    uint8_t statusByte = mr.b0 & 0xF0;
                    int16 ch = (int16)(mr.b0 & 0x0F);

                    if (statusByte == 0x90 && mr.b2 > 0) {
                        Event ev{};
                        ev.busIndex = 0;
                        ev.sampleOffset = delta;
                        ev.type = Event::kNoteOnEvent;
                        ev.flags = Event::kIsLive;
                        ev.noteOn.channel = ch;
                        ev.noteOn.pitch = (int16)mr.b1;
                        ev.noteOn.tuning = 0.0f;
                        ev.noteOn.velocity = (float)mr.b2 / 127.0f;
                        ev.noteOn.length = 0;
                        ev.noteOn.noteId = -1;
                        inEvents.events.push_back(ev);
                    } else if (statusByte == 0x80 || (statusByte == 0x90 && mr.b2 == 0)) {
                        Event ev{};
                        ev.busIndex = 0;
                        ev.sampleOffset = delta;
                        ev.type = Event::kNoteOffEvent;
                        ev.noteOff.channel = ch;
                        ev.noteOff.pitch = (int16)mr.b1;
                        ev.noteOff.velocity = (float)mr.b2 / 127.0f;
                        ev.noteOff.noteId = -1;
                        ev.noteOff.tuning = 0.0f;
                        inEvents.events.push_back(ev);
                    } else if (statusByte == 0xA0) {
                        Event ev{};
                        ev.busIndex = 0;
                        ev.sampleOffset = delta;
                        ev.type = Event::kPolyPressureEvent;
                        ev.polyPressure.channel = ch;
                        ev.polyPressure.pitch = (int16)mr.b1;
                        ev.polyPressure.pressure = (float)mr.b2 / 127.0f;
                        ev.polyPressure.noteId = -1;
                        inEvents.events.push_back(ev);
                    } else if (g_midiMap) {
                        ParamID pid = 0;
                        if (statusByte == 0xB0) {
                            if (g_midiMap->getMidiControllerAssignment(0, ch, (CtrlNumber)mr.b1, pid) == kResultOk) {
                                inParams.addChange(pid, delta, (double)mr.b2 / 127.0);
                            }
                        } else if (statusByte == 0xE0) {
                            if (g_midiMap->getMidiControllerAssignment(0, ch, kPitchBend, pid) == kResultOk) {
                                int raw14 = ((int)mr.b2 << 7) | (int)mr.b1;
                                inParams.addChange(pid, delta, (double)raw14 / 16383.0);
                            }
                        } else if (statusByte == 0xD0) {
                            if (g_midiMap->getMidiControllerAssignment(0, ch, kAfterTouch, pid) == kResultOk) {
                                inParams.addChange(pid, delta, (double)mr.b1 / 127.0);
                            }
                        }
                    }
                    ++midiCursor;
                }

                // Prepare AudioBusBuffers for every bus
                std::vector<AudioBusBuffers> inBuses((size_t)g_numAudioInBuses);
                std::vector<AudioBusBuffers> outBuses((size_t)g_numAudioOutBuses);
                std::vector<std::vector<float*>> inPtrs((size_t)g_numAudioInBuses);
                std::vector<std::vector<float*>> outPtrs((size_t)g_numAudioOutBuses);

                // Input bus 0 copies from inPlanar (or zeros), other input buses get zeroBuf
                for (int32 b = 0; b < g_numAudioInBuses; ++b) {
                    int32 nCh = g_inBusChannels[(size_t)b];
                    inBuses[(size_t)b].numChannels = nCh;
                    inBuses[(size_t)b].silenceFlags = (b == 0 && has_input) ? 0ULL : ((nCh >= 64) ? ~0ULL : ((1ULL << nCh) - 1ULL));
                    inPtrs[(size_t)b].resize((size_t)nCh);
                    for (int32 c = 0; c < nCh; ++c) {
                        if (b == 0 && has_input) {
                            int32 srcC = (c < out_ch) ? c : 0;
                            inPtrs[(size_t)b][(size_t)c] = inPlanar.data() + (size_t)srcC * (size_t)total_frames + (size_t)pos;
                        } else {
                            inPtrs[(size_t)b][(size_t)c] = zeroBuf.data();
                        }
                    }
                    inBuses[(size_t)b].channelBuffers32 = nCh > 0 ? inPtrs[(size_t)b].data() : nullptr;
                }

                // Output bus 0 writes into outPlanar (and auxScratch if plugin has > out_ch channels)
                for (int32 b = 0; b < g_numAudioOutBuses; ++b) {
                    int32 nCh = g_outBusChannels[(size_t)b];
                    outBuses[(size_t)b].numChannels = nCh;
                    outBuses[(size_t)b].silenceFlags = 0;
                    outPtrs[(size_t)b].resize((size_t)nCh);
                    for (int32 c = 0; c < nCh; ++c) {
                        if (b == 0 && c < out_ch) {
                            outPtrs[(size_t)b][(size_t)c] = outPlanar.data() + (size_t)c * (size_t)total_frames + (size_t)pos;
                        } else {
                            float* sc = auxScratch.data() + (size_t)(c % std::max(1, maxChNeeded)) * (size_t)blockCap;
                            memset(sc, 0, sizeof(float) * (size_t)block);
                            outPtrs[(size_t)b][(size_t)c] = sc;
                        }
                    }
                    outBuses[(size_t)b].channelBuffers32 = nCh > 0 ? outPtrs[(size_t)b].data() : nullptr;
                }

                ProcessContext ctx{};
                ctx.state = ProcessContext::kPlaying | ProcessContext::kTempoValid |
                            ProcessContext::kTimeSigValid | ProcessContext::kProjectTimeMusicValid;
                ctx.sampleRate = g_sampleRate;
                ctx.projectTimeSamples = g_projectSamples;
                ctx.tempo = 120.0;
                ctx.timeSigNumerator = 4;
                ctx.timeSigDenominator = 4;
                ctx.projectTimeMusic = ((double)g_projectSamples / g_sampleRate) * (ctx.tempo / 60.0);

                ProcessData pd{};
                pd.processMode = kRealtime;
                pd.symbolicSampleSize = kSample32;
                pd.numSamples = block;
                pd.numInputs = g_numAudioInBuses;
                pd.numOutputs = g_numAudioOutBuses;
                pd.inputs = g_numAudioInBuses > 0 ? inBuses.data() : nullptr;
                pd.outputs = g_numAudioOutBuses > 0 ? outBuses.data() : nullptr;
                pd.inputEvents = &inEvents;
                pd.inputParameterChanges = &inParams;
                pd.processContext = &ctx;

                g_proc->process(pd);

                // If mono plugin wrote only channel 0, duplicate to channel 1 when stereo requested
                if (!g_outBusChannels.empty() && g_outBusChannels[0] == 1 && out_ch >= 2) {
                    memcpy(outPlanar.data() + (size_t)total_frames + (size_t)pos,
                           outPlanar.data() + (size_t)pos,
                           sizeof(float) * (size_t)block);
                }

                g_projectSamples += block;
                pos += block;
            }

            uint32_t ok = 1;
            write_exact(ipc_out, &ok, 4);
            write_exact(ipc_out, &out_ch, 4);
            write_exact(ipc_out, &total_frames, 4);
            if (total_frames > 0) {
                write_exact(ipc_out, outPlanar.data(), sizeof(float) * (size_t)out_ch * (size_t)total_frames);
            }
        }
        else if (cmd == 'Z') {
            g_proc->setProcessing(false);
            g_proc->setProcessing(true);
            uint32_t ok = 1;
            write_exact(ipc_out, &ok, 4);
        }
        else if (cmd == 'S') {
            std::vector<uint8_t> blob = save_full_state();
            uint32_t ok = 1;
            uint32_t sz = (uint32_t)blob.size();
            write_exact(ipc_out, &ok, 4);
            write_exact(ipc_out, &sz, 4);
            if (sz > 0) write_exact(ipc_out, blob.data(), sz);
        }
        else if (cmd == 'L') {
            uint32_t sz = 0;
            if (!read_exact(ipc_in, &sz, 4)) break;
            std::vector<uint8_t> buf(sz);
            if (sz > 0 && !read_exact(ipc_in, buf.data(), sz)) break;
            bool ok_b = restore_full_state(buf.data(), sz);
            uint32_t ok = ok_b ? 1 : 0;
            write_exact(ipc_out, &ok, 4);
        }
        else if (cmd == 'E') {
            if (!g_editorHwnd && g_ctrl) {
                IPlugView* view = g_ctrl->createView(ViewType::kEditor);
                if (view && view->isPlatformTypeSupported(kPlatformTypeHWND) == kResultOk) {
                    ViewRect vr{};
                    if (view->getSize(&vr) != kResultOk || vr.getWidth() <= 0 || vr.getHeight() <= 0) {
                        vr = ViewRect(0, 0, 800, 600);
                    }
                    RECT rc = { 0, 0, vr.getWidth(), vr.getHeight() };
                    AdjustWindowRectEx(&rc, g_editorStyle, FALSE, 0);
                    std::wstring titleW(argv[1]);
                    size_t sl = titleW.find_last_of(L"\\/");
                    if (sl != std::wstring::npos) titleW = titleW.substr(sl + 1);
                    titleW += L" - NovaDAW";

                    g_editorHwnd = CreateWindowExW(
                        0, EDITOR_WND_CLASS, titleW.c_str(), g_editorStyle,
                        CW_USEDEFAULT, CW_USEDEFAULT,
                        rc.right - rc.left, rc.bottom - rc.top,
                        nullptr, nullptr, GetModuleHandleW(nullptr), nullptr
                    );
                    if (g_editorHwnd) {
                        g_plugView = view;
                        view->setFrame(static_cast<IPlugFrame*>(&g_plugFrame));
                        if (view->attached((void*)g_editorHwnd, kPlatformTypeHWND) == kResultOk) {
                            ViewRect vr2{};
                            if (view->getSize(&vr2) == kResultOk && vr2.getWidth() > 0 && vr2.getHeight() > 0) {
                                resize_editor_window(vr2.getWidth(), vr2.getHeight());
                            }
                            ShowWindow(g_editorHwnd, SW_SHOW);
                            SetForegroundWindow(g_editorHwnd);
                        } else {
                            close_editor_internal();
                        }
                    } else {
                        view->release();
                    }
                } else if (view) {
                    view->release();
                }
            }
            std::vector<uint8_t> blob = save_full_state();
            uint32_t ok = 1;
            uint32_t sz = (uint32_t)blob.size();
            write_exact(ipc_out, &ok, 4);
            write_exact(ipc_out, &sz, 4);
            if (sz > 0) write_exact(ipc_out, blob.data(), sz);
        }
        else if (cmd == 'T') {
            uint32_t ok = 1;
            uint32_t is_open = (g_editorHwnd != nullptr && IsWindow(g_editorHwnd)) ? 1 : 0;
            if (!is_open) {
                close_editor_internal();
            }
            write_exact(ipc_out, &ok, 4);
            write_exact(ipc_out, &is_open, 4);
        }
        else if (cmd == 'C') {
            close_editor_internal();
            uint32_t ok = 1;
            write_exact(ipc_out, &ok, 4);
        }
        else if (cmd == 'Q') {
            close_editor_internal();
            uint32_t ok = 1;
            write_exact(ipc_out, &ok, 4);
            break;
        }

        SetEvent(g_hNextCmdAllowed);
    }

    close_editor_internal();
    g_proc->setProcessing(false);
    g_comp->setActive(false);
    if (g_compCP && g_ctrlCP) { g_compCP->disconnect(g_ctrlCP); g_ctrlCP->disconnect(g_compCP); }
    if (g_compCP) g_compCP->release();
    if (g_ctrlCP) g_ctrlCP->release();
    if (g_midiMap) g_midiMap->release();
    if (g_ctrl) { if (g_ctrlIsSeparate) g_ctrl->terminate(); g_ctrl->release(); }
    g_proc->release();
    g_comp->terminate();
    g_comp->release();
    g_factory->release();
    if (exitDll) exitDll();
    CoUninitialize();
    ExitProcess(0);
    return 0;
}
