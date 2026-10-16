/*
 * core/bin/vst2_bridge_x86.c
 * Standalone 32-bit (x86) VST 2.4 Host Bridge for NovaDAW on 64-bit Windows.
 * Compiled with /MT (static CRT) for zero external dependencies on any Windows PC.
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <io.h>
#include <fcntl.h>

#pragma pack(push, 8)

struct AEffect;

typedef intptr_t (__cdecl *audioMasterCallback)(
    struct AEffect* effect, int32_t opcode, int32_t index,
    intptr_t value, void* ptr, float opt
);
typedef intptr_t (__cdecl *AEffectDispatcherProc)(
    struct AEffect* effect, int32_t opcode, int32_t index,
    intptr_t value, void* ptr, float opt
);
typedef void (__cdecl *AEffectProcessProc)(
    struct AEffect* effect, float** inputs, float** outputs, int32_t sampleFrames
);
typedef void (__cdecl *AEffectProcessDoubleProc)(
    struct AEffect* effect, double** inputs, double** outputs, int32_t sampleFrames
);
typedef void (__cdecl *AEffectSetParameterProc)(
    struct AEffect* effect, int32_t index, float parameter
);
typedef float (__cdecl *AEffectGetParameterProc)(
    struct AEffect* effect, int32_t index
);

typedef struct AEffect {
    int32_t magic;
    AEffectDispatcherProc dispatcher;
    AEffectProcessProc process;
    AEffectSetParameterProc setParameter;
    AEffectGetParameterProc getParameter;
    int32_t numPrograms;
    int32_t numParams;
    int32_t numInputs;
    int32_t numOutputs;
    int32_t flags;
    intptr_t resvd1;
    intptr_t resvd2;
    int32_t initialDelay;
    int32_t realQualities;
    int32_t offQualities;
    float ioRatio;
    void* object;
    void* user;
    int32_t uniqueID;
    int32_t version;
    AEffectProcessProc processReplacing;
    AEffectProcessDoubleProc processDoubleReplacing;
    char future[56];
} AEffect;

typedef struct ERect {
    int16_t top;
    int16_t left;
    int16_t bottom;
    int16_t right;
} ERect;

typedef struct VstMidiEvent {
    int32_t type;
    int32_t byteSize;
    int32_t deltaFrames;
    int32_t flags;
    int32_t noteLength;
    int32_t noteOffset;
    char midiData[4];
    char detune;
    char noteOffVelocity;
    char reserved1;
    char reserved2;
} VstMidiEvent;

typedef struct VstEvents {
    int32_t numEvents;
    intptr_t reserved;
    void* events[2048];
} VstEvents;

typedef struct VstTimeInfo {
    double samplePos;
    double sampleRate;
    double nanoSeconds;
    double ppqPos;
    double tempo;
    double barStartPos;
    double cycleStartPos;
    double cycleEndPos;
    int32_t timeSigNumerator;
    int32_t timeSigDenominator;
    int32_t smpteOffset;
    int32_t smpteFrameRate;
    int32_t samplesToNextClock;
    int32_t flags;
} VstTimeInfo;

typedef struct MidiMsgRecord {
    int32_t sample_offset;
    uint8_t b0;
    uint8_t b1;
    uint8_t b2;
    uint8_t pad;
} MidiMsgRecord;

#pragma pack(pop)

static VstTimeInfo g_time_info = {
    0.0, 44100.0, 0.0, 0.0, 120.0, 0.0, 0.0, 0.0, 4, 4, 0, 0, 0, (1 << 10) | (1 << 13)
};
static float g_sample_rate = 44100.0f;
static int32_t g_block_size = 512;
static HWND g_editor_hwnd = NULL;
static AEffect* g_effect = NULL;
static DWORD g_window_style = WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX;

static void resize_editor_window(int width, int height) {
    if (!g_editor_hwnd || width <= 0 || height <= 0) return;
    RECT rc = { 0, 0, width, height };
    AdjustWindowRectEx(&rc, g_window_style, FALSE, 0);
    SetWindowPos(
        g_editor_hwnd, NULL, 0, 0,
        rc.right - rc.left, rc.bottom - rc.top,
        SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE
    );
}

static intptr_t __cdecl host_callback(
    AEffect* effect, int32_t opcode, int32_t index,
    intptr_t value, void* ptr, float opt
) {
    switch (opcode) {
        case 1: /* audioMasterVersion */
            return 2400;
        case 3: /* audioMasterIdle */
            return 1;
        case 7: /* audioMasterGetTime */
            g_time_info.sampleRate = (double)g_sample_rate;
            return (intptr_t)&g_time_info;
        case 8: /* audioMasterProcessEvents */
            return 1;
        case 15: /* audioMasterSizeWindow */
            resize_editor_window((int)index, (int)value);
            return 1;
        case 16: /* audioMasterGetSampleRate */
            return (intptr_t)g_sample_rate;
        case 17: /* audioMasterGetBlockSize */
            return (intptr_t)g_block_size;
        case 23: /* audioMasterGetCurrentProcessLevel */
            return 0;
        case 24: /* audioMasterGetAutomationState */
            return 1;
        case 32: /* audioMasterGetVendorString */
        case 33: /* audioMasterGetProductString */
            if (ptr) {
                strcpy((char*)ptr, "NovaDAW");
                return 1;
            }
            return 0;
        case 34: /* audioMasterGetVendorVersion */
            return 1000;
        case 37: /* audioMasterCanDo */
            return 1;
        case 38: /* audioMasterGetLanguage */
            return 1;
        case 42: /* audioMasterUpdateDisplay */
            return 1;
        default:
            return 0;
    }
}

static void close_editor_internal(void) {
    if (g_editor_hwnd) {
        HWND hwnd = g_editor_hwnd;
        g_editor_hwnd = NULL;
        if (g_effect && g_effect->dispatcher) {
            g_effect->dispatcher(g_effect, 15, 0, 0, NULL, 0.0f); /* effEditClose */
        }
        DestroyWindow(hwnd);
    }
}

static LRESULT CALLBACK editor_wnd_proc(HWND hwnd, UINT msg, WPARAM wparam, LPARAM lparam) {
    if (msg == WM_CLOSE) {
        close_editor_internal();
        return 0;
    }
    return DefWindowProcW(hwnd, msg, wparam, lparam);
}

static int read_exact(int fd, void* buf, size_t count) {
    uint8_t* p = (uint8_t*)buf;
    size_t remaining = count;
    while (remaining > 0) {
        int n = _read(fd, p, (unsigned int)(remaining > 65536 ? 65536 : remaining));
        if (n <= 0) return 0;
        p += n;
        remaining -= (size_t)n;
    }
    return 1;
}

static int write_exact(int fd, const void* buf, size_t count) {
    const uint8_t* p = (const uint8_t*)buf;
    size_t remaining = count;
    while (remaining > 0) {
        int n = _write(fd, p, (unsigned int)(remaining > 65536 ? 65536 : remaining));
        if (n <= 0) return 0;
        p += n;
        remaining -= (size_t)n;
    }
    return 1;
}

static void write_state_chunk(int ipc_out) {
    uint32_t status = 1;
    if (g_effect->flags & (1 << 5)) { /* effFlagsProgramChunks */
        void* chunk_ptr = NULL;
        intptr_t sz = g_effect->dispatcher(g_effect, 23, 0, 0, &chunk_ptr, 0.0f);
        if (sz > 0 && chunk_ptr != NULL) {
            uint32_t u_sz = (uint32_t)sz;
            write_exact(ipc_out, &status, 4);
            write_exact(ipc_out, &u_sz, 4);
            write_exact(ipc_out, chunk_ptr, u_sz);
            return;
        }
    }
    int32_t n_params = g_effect->numParams > 0 ? g_effect->numParams : 0;
    uint32_t u_sz = (uint32_t)(n_params * sizeof(float));
    float* vals = u_sz > 0 ? (float*)malloc(u_sz) : NULL;
    if (vals && g_effect->getParameter) {
        for (int32_t i = 0; i < n_params; ++i) {
            vals[i] = g_effect->getParameter(g_effect, i);
        }
    }
    write_exact(ipc_out, &status, 4);
    write_exact(ipc_out, &u_sz, 4);
    if (u_sz > 0 && vals) {
        write_exact(ipc_out, vals, u_sz);
    }
    if (vals) free(vals);
}

int wmain(int argc, wchar_t* argv[]) {
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOGPFAULTERRORBOX | SEM_NOOPENFILEERRORBOX);
    _setmode(_fileno(stdin), _O_BINARY);
    _setmode(_fileno(stdout), _O_BINARY);

    int ipc_in = _fileno(stdin);
    int ipc_out = _dup(_fileno(stdout));
    _setmode(ipc_out, _O_BINARY);
    /* Redirect stdout to stderr so plugin printf calls never corrupt ipc_out */
    _dup2(_fileno(stderr), _fileno(stdout));

    if (argc < 2) {
        uint32_t status = 0;
        const char* err = "Missing DLL path argument";
        uint32_t len = (uint32_t)strlen(err);
        write_exact(ipc_out, &status, 4);
        write_exact(ipc_out, &len, 4);
        write_exact(ipc_out, err, len);
        return 1;
    }

    int probe_only = (argc >= 3 && wcscmp(argv[2], L"--probe") == 0);

    /* Add DLL directory to search path for companion DLLs */
    wchar_t dir_buf[MAX_PATH];
    wcsncpy(dir_buf, argv[1], MAX_PATH - 1);
    dir_buf[MAX_PATH - 1] = L'\0';
    wchar_t* last_slash = wcsrchr(dir_buf, L'\\');
    if (last_slash) {
        *last_slash = L'\0';
        SetDllDirectoryW(dir_buf);
    }

    HMODULE hDll = LoadLibraryW(argv[1]);
    if (!hDll) {
        uint32_t status = 0;
        char err[256];
        snprintf(err, sizeof(err), "LoadLibraryW failed (Win32 error %lu)", GetLastError());
        uint32_t len = (uint32_t)strlen(err);
        write_exact(ipc_out, &status, 4);
        write_exact(ipc_out, &len, 4);
        write_exact(ipc_out, err, len);
        return 1;
    }

    typedef AEffect* (__cdecl *VSTPluginMainProc)(audioMasterCallback);
    VSTPluginMainProc main_proc = (VSTPluginMainProc)GetProcAddress(hDll, "VSTPluginMain");
    if (!main_proc) main_proc = (VSTPluginMainProc)GetProcAddress(hDll, "main");
    if (!main_proc) main_proc = (VSTPluginMainProc)GetProcAddress(hDll, "MAIN");

    if (!main_proc) {
        uint32_t status = 0;
        const char* err = "No VSTPluginMain or main export found in 32-bit DLL";
        uint32_t len = (uint32_t)strlen(err);
        write_exact(ipc_out, &status, 4);
        write_exact(ipc_out, &len, 4);
        write_exact(ipc_out, err, len);
        return 1;
    }

    g_effect = main_proc(host_callback);
    if (!g_effect || g_effect->magic != 0x56737450) {
        uint32_t status = 0;
        const char* err = "Invalid AEffect instance returned by 32-bit VST2 plugin";
        uint32_t len = (uint32_t)strlen(err);
        write_exact(ipc_out, &status, 4);
        write_exact(ipc_out, &len, 4);
        write_exact(ipc_out, err, len);
        return 1;
    }

    char eff_name[128];
    memset(eff_name, 0, sizeof(eff_name));
    if (!probe_only) {
        g_effect->dispatcher(g_effect, 0, 0, 0, NULL, 0.0f); /* effOpen */
        g_effect->dispatcher(g_effect, 10, 0, 0, NULL, g_sample_rate); /* effSetSampleRate */
        g_effect->dispatcher(g_effect, 11, 0, g_block_size, NULL, 0.0f); /* effSetBlockSize */
        g_effect->dispatcher(g_effect, 12, 0, 1, NULL, 0.0f); /* effMainsChanged(1) */
        g_effect->dispatcher(g_effect, 71, 0, 0, NULL, 0.0f); /* effStartProcess */
        g_effect->dispatcher(g_effect, 45, 0, 0, eff_name, 0.0f); /* effGetEffectName */
        g_effect->dispatcher(g_effect, 2, 0, 0, NULL, 0.0f); /* effSetProgram(0) */
        ERect* er_warm = NULL;
        g_effect->dispatcher(g_effect, 13, 0, 0, &er_warm, 0.0f); /* effEditGetRect */
        if (g_effect->flags & (1 << 5)) {
            void* chunk_warm = NULL;
            g_effect->dispatcher(g_effect, 23, 0, 0, &chunk_warm, 0.0f); /* effGetChunk */
        }
    }

    uint32_t status = 1;
    int32_t is_synth = (g_effect->flags & (1 << 8)) ? 1 : 0;
    int32_t num_inputs = g_effect->numInputs;
    int32_t num_outputs = g_effect->numOutputs;
    int32_t num_params = g_effect->numParams;
    uint32_t name_len = (uint32_t)strlen(eff_name);

    write_exact(ipc_out, &status, 4);
    write_exact(ipc_out, &is_synth, 4);
    write_exact(ipc_out, &num_inputs, 4);
    write_exact(ipc_out, &num_outputs, 4);
    write_exact(ipc_out, &num_params, 4);
    write_exact(ipc_out, &name_len, 4);
    if (name_len > 0) {
        write_exact(ipc_out, eff_name, name_len);
    }

    if (probe_only) {
        ExitProcess(0);
    }

    /* Register Win32 editor window class */
    WNDCLASSEXW wc;
    memset(&wc, 0, sizeof(wc));
    wc.cbSize = sizeof(WNDCLASSEXW);
    wc.style = CS_HREDRAW | CS_VREDRAW;
    wc.lpfnWndProc = editor_wnd_proc;
    wc.hInstance = GetModuleHandleW(NULL);
    wc.hCursor = LoadCursorW(NULL, MAKEINTRESOURCEW(32512));
    wc.hbrBackground = (HBRUSH)(COLOR_WINDOW + 1);
    wc.lpszClassName = L"NovaDAWVST2Bridge32Wnd";
    RegisterClassExW(&wc);

    HANDLE hStdin = GetStdHandle(STD_INPUT_HANDLE);

    while (1) {
        if (g_editor_hwnd) {
            MSG msg;
            while (PeekMessageW(&msg, NULL, 0, 0, PM_REMOVE)) {
                TranslateMessage(&msg);
                DispatchMessageW(&msg);
            }
            if (g_editor_hwnd && g_effect && g_effect->dispatcher) {
                g_effect->dispatcher(g_effect, 19, 0, 0, NULL, 0.0f); /* effEditIdle */
            }
            DWORD avail = 0;
            if (!PeekNamedPipe(hStdin, NULL, 0, NULL, &avail, NULL)) {
                break;
            }
            if (avail == 0) {
                Sleep(12);
                continue;
            }
        }

        uint8_t cmd = 0;
        if (!read_exact(ipc_in, &cmd, 1)) {
            break;
        }

        if (cmd == 'R') { /* Render */
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

            if (sr > 0.0f && sr != g_sample_rate) {
                g_sample_rate = sr;
                g_effect->dispatcher(g_effect, 10, 0, 0, NULL, g_sample_rate);
            }
            if (reset_flag) {
                g_effect->dispatcher(g_effect, 12, 0, 0, NULL, 0.0f);
                g_effect->dispatcher(g_effect, 12, 0, 1, NULL, 0.0f);
            }

            MidiMsgRecord* midi_recs = NULL;
            if (num_midi > 0) {
                midi_recs = (MidiMsgRecord*)malloc(sizeof(MidiMsgRecord) * num_midi);
                if (!read_exact(ipc_in, midi_recs, sizeof(MidiMsgRecord) * num_midi)) {
                    free(midi_recs);
                    break;
                }
            }

            int32_t out_ch = req_ch > 0 ? req_ch : 2;
            float* in_planar = NULL;
            if (has_input && total_frames > 0) {
                in_planar = (float*)malloc(sizeof(float) * out_ch * total_frames);
                if (!read_exact(ipc_in, in_planar, sizeof(float) * out_ch * total_frames)) {
                    if (midi_recs) free(midi_recs);
                    free(in_planar);
                    break;
                }
            }

            float* out_planar = total_frames > 0 ? (float*)calloc(out_ch * total_frames, sizeof(float)) : NULL;
            int32_t n_in = g_effect->numInputs > out_ch ? g_effect->numInputs : out_ch;
            int32_t n_out = g_effect->numOutputs > out_ch ? g_effect->numOutputs : out_ch;
            if (n_in < 2) n_in = 2;
            if (n_out < 2) n_out = 2;

            float** in_ptrs = (float**)malloc(sizeof(float*) * n_in);
            float** out_ptrs = (float**)malloc(sizeof(float*) * n_out);
            for (int32_t c = 0; c < n_in; ++c) in_ptrs[c] = (float*)calloc(g_block_size, sizeof(float));
            for (int32_t c = 0; c < n_out; ++c) out_ptrs[c] = (float*)calloc(g_block_size, sizeof(float));

            int32_t midi_idx = 0;
            int32_t pos = 0;
            VstEvents ev_container;
            VstMidiEvent midi_pool[2048];

            while (pos < total_frames) {
                int32_t block = total_frames - pos;
                if (block > g_block_size) block = g_block_size;

                /* Prepare input buffers */
                for (int32_t c = 0; c < n_in; ++c) {
                    if (in_planar && c < out_ch) {
                        memcpy(in_ptrs[c], in_planar + c * total_frames + pos, sizeof(float) * block);
                    } else {
                        memset(in_ptrs[c], 0, sizeof(float) * block);
                    }
                }
                for (int32_t c = 0; c < n_out; ++c) {
                    memset(out_ptrs[c], 0, sizeof(float) * block);
                }

                /* Collect MIDI events in [pos, pos + block) */
                int32_t ev_count = 0;
                while (midi_idx < num_midi && midi_recs[midi_idx].sample_offset < pos + block) {
                    if (ev_count < 2048) {
                        VstMidiEvent* mev = &midi_pool[ev_count];
                        memset(mev, 0, sizeof(VstMidiEvent));
                        mev->type = 1;
                        mev->byteSize = sizeof(VstMidiEvent);
                        int32_t delta = midi_recs[midi_idx].sample_offset - pos;
                        if (delta < 0) delta = 0;
                        if (delta >= block) delta = block - 1;
                        mev->deltaFrames = delta;
                        mev->flags = 1;
                        mev->midiData[0] = (char)midi_recs[midi_idx].b0;
                        mev->midiData[1] = (char)midi_recs[midi_idx].b1;
                        mev->midiData[2] = (char)midi_recs[midi_idx].b2;
                        ev_container.events[ev_count] = mev;
                        ev_count++;
                    }
                    midi_idx++;
                }
                if (ev_count > 0) {
                    ev_container.numEvents = ev_count;
                    ev_container.reserved = 0;
                    g_effect->dispatcher(g_effect, 25, 0, 0, &ev_container, 0.0f);
                }

                if (g_effect->processReplacing) {
                    g_effect->processReplacing(g_effect, in_ptrs, out_ptrs, block);
                } else if (g_effect->process) {
                    g_effect->process(g_effect, in_ptrs, out_ptrs, block);
                }

                for (int32_t c = 0; c < out_ch; ++c) {
                    if (c < n_out) {
                        memcpy(out_planar + c * total_frames + pos, out_ptrs[c], sizeof(float) * block);
                    }
                }
                pos += block;
            }

            uint32_t ok = 1;
            write_exact(ipc_out, &ok, 4);
            write_exact(ipc_out, &out_ch, 4);
            write_exact(ipc_out, &total_frames, 4);
            if (total_frames > 0 && out_planar) {
                write_exact(ipc_out, out_planar, sizeof(float) * out_ch * total_frames);
            }

            for (int32_t c = 0; c < n_in; ++c) free(in_ptrs[c]);
            for (int32_t c = 0; c < n_out; ++c) free(out_ptrs[c]);
            free(in_ptrs);
            free(out_ptrs);
            if (out_planar) free(out_planar);
            if (in_planar) free(in_planar);
            if (midi_recs) free(midi_recs);
        }
        else if (cmd == 'Z') { /* Reset */
            g_effect->dispatcher(g_effect, 12, 0, 0, NULL, 0.0f);
            g_effect->dispatcher(g_effect, 12, 0, 1, NULL, 0.0f);
            uint32_t ok = 1;
            write_exact(ipc_out, &ok, 4);
        }
        else if (cmd == 'S') { /* State */
            write_state_chunk(ipc_out);
        }
        else if (cmd == 'L') { /* Restore */
            uint32_t sz = 0;
            if (!read_exact(ipc_in, &sz, 4)) break;
            uint8_t* buf = sz > 0 ? (uint8_t*)malloc(sz) : NULL;
            if (sz > 0 && !read_exact(ipc_in, buf, sz)) {
                free(buf);
                break;
            }
            if (sz > 0 && buf) {
                if (g_effect->flags & (1 << 5)) {
                    g_effect->dispatcher(g_effect, 24, 0, (intptr_t)sz, buf, 0.0f);
                } else if (g_effect->setParameter) {
                    int32_t count = (int32_t)(sz / sizeof(float));
                    if (count > g_effect->numParams) count = g_effect->numParams;
                    float* fvals = (float*)buf;
                    for (int32_t i = 0; i < count; ++i) {
                        g_effect->setParameter(g_effect, i, fvals[i]);
                    }
                }
            }
            if (buf) free(buf);
            uint32_t ok = 1;
            write_exact(ipc_out, &ok, 4);
        }
        else if (cmd == 'E') { /* Open Editor */
            if (!g_editor_hwnd) {
                ERect* er = NULL;
                g_effect->dispatcher(g_effect, 13, 0, 0, &er, 0.0f);
                int w = (er && (er->right - er->left) > 0) ? (er->right - er->left) : 800;
                int h = (er && (er->bottom - er->top) > 0) ? (er->bottom - er->top) : 600;
                RECT rc = { 0, 0, w, h };
                AdjustWindowRectEx(&rc, g_window_style, FALSE, 0);
                g_editor_hwnd = CreateWindowExW(
                    0, L"NovaDAWVST2Bridge32Wnd", argv[1], g_window_style,
                    CW_USEDEFAULT, CW_USEDEFAULT,
                    rc.right - rc.left, rc.bottom - rc.top,
                    NULL, NULL, GetModuleHandleW(NULL), NULL
                );
                g_effect->dispatcher(g_effect, 14, 0, 0, (void*)g_editor_hwnd, 0.0f);
                ERect* er2 = NULL;
                g_effect->dispatcher(g_effect, 13, 0, 0, &er2, 0.0f);
                if (er2 && (er2->right - er2->left) > 0 && (er2->bottom - er2->top) > 0) {
                    resize_editor_window(er2->right - er2->left, er2->bottom - er2->top);
                }
                ShowWindow(g_editor_hwnd, SW_SHOW);
                SetForegroundWindow(g_editor_hwnd);
            }
            write_state_chunk(ipc_out);
        }
        else if (cmd == 'T') { /* Editor status */
            uint32_t ok = 1;
            uint32_t is_open = (g_editor_hwnd != NULL && IsWindow(g_editor_hwnd)) ? 1 : 0;
            if (!is_open) g_editor_hwnd = NULL;
            write_exact(ipc_out, &ok, 4);
            write_exact(ipc_out, &is_open, 4);
        }
        else if (cmd == 'C') { /* Close editor */
            close_editor_internal();
            uint32_t ok = 1;
            write_exact(ipc_out, &ok, 4);
        }
        else if (cmd == 'Q') { /* Quit */
            close_editor_internal();
            uint32_t ok = 1;
            write_exact(ipc_out, &ok, 4);
            break;
        }
    }

    ExitProcess(0);
    return 0;
}
