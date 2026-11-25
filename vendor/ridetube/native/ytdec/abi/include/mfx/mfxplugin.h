// SPDX-License-Identifier: MIT
// Copyright (c) 2007-2019 Intel Corporation (abi/include/mfx/LICENSE)
/* Intel Media SDK 1.x API: user plugins (ytdec: the unit's VP9 decoder).
 *
 * Subset of Intel's mfxplugin.h (MIT licence, Copyright (c) 2007-2019 Intel
 * Corporation; compared with MediaSDK intel-mediasdk-18.4.1 api/include).
 * Pointer members the decoder never calls are void*; sizes are Intel's on
 * x86-64 (checked below): CreatePlugin and GetPluginParam write whole structs. */
#ifndef WOC_MFXPLUGIN_H
#define WOC_MFXPLUGIN_H

#include "mfxvideo.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    mfxU8 Data[16];
} mfxPluginUID;

/* /etc/mediasdk/plugins.cfg on the unit: [VP9d_a922394d8d87452f878c51f2fc9b4131] */
static const mfxPluginUID MFX_PLUGINID_VP9D_HW = {{0xa9, 0x22, 0x39, 0x4d, 0x8d, 0x87, 0x45, 0x2f,
                                                    0x87, 0x8c, 0x51, 0xf2, 0xfc, 0x9b, 0x41, 0x31}};

enum { MFX_PLUGINTYPE_VIDEO_DECODE = 1 };

typedef struct mfxPluginParam {
    mfxU32 reserved[6];
    mfxU16 reserved1;
    mfxU16 PluginVersion;
    mfxVersion APIVersion;
    mfxPluginUID PluginUID;
    mfxU32 Type;
    mfxU32 CodecId;
    mfxU32 ThreadPolicy;   /* mfxThreadPolicy, an int enum */
    mfxU32 MaxThreadNum;
} mfxPluginParam;

typedef struct mfxPlugin {
    mfxHDL pthis;
    void *PluginInit;
    void *PluginClose;
    mfxStatus (*GetPluginParam)(mfxHDL pthis, mfxPluginParam *par);
    void *Submit;
    void *Execute;
    void *FreeResources;
    void *Video;   /* union { mfxVideoCodecPlugin *Video; mfxAudioCodecPlugin *Audio; } */
    mfxHDL reserved[8];
} mfxPlugin;

/* exported by the plugin library (CreatePlugin@@LIBMFXSW_PLUGIN); the UID by value, as the dispatcher calls it */
typedef mfxStatus (*mfxCreatePluginFn)(mfxPluginUID uid, mfxPlugin *plugin);

mfxStatus MFXVideoUSER_Register(mfxSession session, mfxU32 type, const mfxPlugin *par);
mfxStatus MFXVideoUSER_Unregister(mfxSession session, mfxU32 type);

#ifdef __cplusplus
}
static_assert(sizeof(mfxPluginParam) == 64, "mfxPluginParam is 64 bytes in Intel's header");
static_assert(sizeof(mfxPlugin) == 128, "mfxPlugin is 128 bytes in Intel's header");
#endif

#endif
