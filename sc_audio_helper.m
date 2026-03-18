/**
 * sc_audio_helper — Captures system audio via ScreenCaptureKit.
 * Writes raw float32 INTERLEAVED stereo PCM at 48 kHz to a file.
 * Usage: sc_audio_helper <output_path> [sample_rate]
 * Send SIGTERM or SIGINT to stop cleanly.
 *
 * IMPORTANT: ScreenCaptureKit delivers audio as NON-INTERLEAVED (planar)
 * Float32. This helper detects the format and interleaves the channels
 * before writing, so the Python side can read simple [L,R,L,R,...] data.
 *
 * Compile:
 *   clang -O2 -fobjc-arc -framework Foundation -framework ScreenCaptureKit \
 *         -framework CoreMedia -framework CoreAudio \
 *         sc_audio_helper.m -o sc_audio_helper
 */

#import <Foundation/Foundation.h>
#import <ScreenCaptureKit/ScreenCaptureKit.h>
#import <CoreMedia/CoreMedia.h>
#import <CoreAudio/CoreAudioTypes.h>

@interface AudioCapture : NSObject <SCStreamOutput, SCStreamDelegate>
@property (nonatomic, strong) NSString *outputPath;
@property (nonatomic, assign) int sampleRate;
@property (nonatomic, strong) SCStream *stream;
@property (nonatomic, strong) NSFileHandle *fileHandle;
@property (nonatomic, assign) BOOL isRunning;
@property (nonatomic, assign) BOOL loggedFormat;
@end

@implementation AudioCapture

- (void)startWithCompletion:(void (^)(BOOL success))completion {
    [[NSFileManager defaultManager] createFileAtPath:self.outputPath
                                            contents:nil
                                          attributes:nil];
    self.fileHandle = [NSFileHandle fileHandleForWritingAtPath:self.outputPath];

    [SCShareableContent
        getShareableContentExcludingDesktopWindows:NO
                                onScreenWindowsOnly:YES
                                  completionHandler:^(SCShareableContent *content,
                                                      NSError *error) {
        if (error || content.displays.count == 0) {
            fprintf(stderr, "No content: %s\n",
                    error.localizedDescription.UTF8String ?: "no displays");
            completion(NO);
            return;
        }

        SCDisplay *display = content.displays.firstObject;
        SCContentFilter *filter =
            [[SCContentFilter alloc] initWithDisplay:display
                                    excludingWindows:@[]];

        SCStreamConfiguration *config =
            [[SCStreamConfiguration alloc] init];
        config.capturesAudio = YES;
        config.excludesCurrentProcessAudio = YES;
        config.sampleRate = self.sampleRate;
        config.channelCount = 2;
        /* Minimal video — SCStream requires it, we discard frames */
        config.width = 2;
        config.height = 2;
        config.minimumFrameInterval = CMTimeMake(1, 1);

        self.stream = [[SCStream alloc] initWithFilter:filter
                                         configuration:config
                                              delegate:self];

        NSError *addErr = nil;
        dispatch_queue_t aq =
            dispatch_queue_create("audio", DISPATCH_QUEUE_SERIAL);
        dispatch_queue_t sq =
            dispatch_queue_create("screen", DISPATCH_QUEUE_SERIAL);

        [self.stream addStreamOutput:self
                                type:SCStreamOutputTypeAudio
                  sampleHandlerQueue:aq
                               error:&addErr];
        if (addErr) {
            fprintf(stderr, "addStreamOutput(audio) failed: %s\n",
                    addErr.localizedDescription.UTF8String);
            completion(NO);
            return;
        }
        /* Screen output too — some macOS versions require it for audio */
        [self.stream addStreamOutput:self
                                type:SCStreamOutputTypeScreen
                  sampleHandlerQueue:sq
                               error:nil];

        [self.stream startCaptureWithCompletionHandler:^(NSError *err) {
            if (err) {
                fprintf(stderr, "startCapture failed: %s\n",
                        err.localizedDescription.UTF8String);
                completion(NO);
                return;
            }
            self.isRunning = YES;
            fprintf(stderr, "READY\n");
            fflush(stderr);
            completion(YES);
        }];
    }];
}

- (void)stop {
    if (!self.isRunning) return;
    self.isRunning = NO;

    dispatch_semaphore_t sem = dispatch_semaphore_create(0);
    [self.stream stopCaptureWithCompletionHandler:^(NSError *err) {
        dispatch_semaphore_signal(sem);
    }];
    dispatch_semaphore_wait(sem,
        dispatch_time(DISPATCH_TIME_NOW, 5 * NSEC_PER_SEC));

    [self.fileHandle closeFile];
    self.fileHandle = nil;
}

#pragma mark - SCStreamOutput

- (void)stream:(SCStream *)stream
    didOutputSampleBuffer:(CMSampleBufferRef)sampleBuffer
                   ofType:(SCStreamOutputType)type {
    if (type != SCStreamOutputTypeAudio || !self.isRunning) return;

    /* --- Get audio format description --- */
    CMFormatDescriptionRef formatDesc =
        CMSampleBufferGetFormatDescription(sampleBuffer);
    if (!formatDesc) return;

    const AudioStreamBasicDescription *asbd =
        CMAudioFormatDescriptionGetStreamBasicDescription(formatDesc);
    if (!asbd) return;

    /* Log format once for debugging */
    if (!self.loggedFormat) {
        self.loggedFormat = YES;
        BOOL nonInterleaved =
            (asbd->mFormatFlags & kAudioFormatFlagIsNonInterleaved) != 0;
        fprintf(stderr,
                "FORMAT: %.0f Hz, %u ch, %u bits, flags=0x%x, "
                "interleaved=%s\n",
                asbd->mSampleRate,
                (unsigned)asbd->mChannelsPerFrame,
                (unsigned)asbd->mBitsPerChannel,
                (unsigned)asbd->mFormatFlags,
                nonInterleaved ? "NO (planar)" : "YES");
        fflush(stderr);
    }

    CMItemCount numFrames = CMSampleBufferGetNumSamples(sampleBuffer);
    if (numFrames == 0) return;

    UInt32 channels = asbd->mChannelsPerFrame;
    if (channels == 0) channels = 2;
    BOOL isNonInterleaved =
        (asbd->mFormatFlags & kAudioFormatFlagIsNonInterleaved) != 0;

    if (isNonInterleaved) {
        /* --- Non-interleaved (planar): each channel in its own buffer ---
         * ScreenCaptureKit typically delivers audio this way.
         * We must interleave [L0,R0,L1,R1,...] for the Python reader.
         */
        AudioBufferList *abl = NULL;
        CMBlockBufferRef retainedBlock = NULL;
        size_t ablSize = 0;

        /* First call: get required buffer list size */
        OSStatus st = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
            sampleBuffer, &ablSize, NULL, 0,
            NULL, NULL,
            kCMSampleBufferFlag_AudioBufferList_Assure16ByteAlignment,
            NULL);
        if (st != noErr && st != kCMBlockBufferInsufficientSpaceErr) return;

        abl = (AudioBufferList *)calloc(1, ablSize);
        if (!abl) return;

        st = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
            sampleBuffer, NULL, abl, ablSize,
            NULL, NULL,
            kCMSampleBufferFlag_AudioBufferList_Assure16ByteAlignment,
            &retainedBlock);

        if (st != noErr) {
            free(abl);
            if (retainedBlock) CFRelease(retainedBlock);
            return;
        }

        /* Interleave channels into a single buffer */
        UInt32 actualCh = MIN(channels, abl->mNumberBuffers);
        size_t outBytes = (size_t)numFrames * actualCh * sizeof(float);
        float *interleaved = (float *)malloc(outBytes);
        if (!interleaved) {
            free(abl);
            if (retainedBlock) CFRelease(retainedBlock);
            return;
        }

        for (UInt32 ch = 0; ch < actualCh; ch++) {
            const float *src = (const float *)abl->mBuffers[ch].mData;
            UInt32 srcFrames = abl->mBuffers[ch].mDataByteSize / sizeof(float);
            UInt32 frames = MIN((UInt32)numFrames, srcFrames);
            for (UInt32 f = 0; f < frames; f++) {
                interleaved[f * actualCh + ch] = src[f];
            }
            /* Zero-fill if this channel has fewer frames */
            for (UInt32 f = frames; f < (UInt32)numFrames; f++) {
                interleaved[f * actualCh + ch] = 0.0f;
            }
        }

        NSData *data = [NSData dataWithBytesNoCopy:interleaved
                                            length:outBytes
                                      freeWhenDone:YES];
        [self.fileHandle writeData:data];

        free(abl);
        if (retainedBlock) CFRelease(retainedBlock);

    } else {
        /* --- Already interleaved: write raw bytes directly --- */
        CMBlockBufferRef blockBuf = CMSampleBufferGetDataBuffer(sampleBuffer);
        if (!blockBuf) return;

        size_t length = CMBlockBufferGetDataLength(blockBuf);
        if (length == 0) return;

        NSMutableData *data = [NSMutableData dataWithLength:length];
        OSStatus status =
            CMBlockBufferCopyDataBytes(blockBuf, 0, length, data.mutableBytes);
        if (status != kCMBlockBufferNoErr) return;

        [self.fileHandle writeData:data];
    }
}

#pragma mark - SCStreamDelegate

- (void)stream:(SCStream *)stream didStopWithError:(NSError *)error {
    fprintf(stderr, "Stream stopped: %s\n",
            error.localizedDescription.UTF8String);
}

@end

/* ------------------------------------------------------------------ */

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc < 2) {
            fprintf(stderr,
                    "Usage: sc_audio_helper <output_path> [sample_rate]\n");
            return 1;
        }

        NSString *outputPath =
            [NSString stringWithUTF8String:argv[1]];
        int sampleRate = argc >= 3 ? atoi(argv[2]) : 48000;
        if (sampleRate <= 0) sampleRate = 48000;

        AudioCapture *capture = [[AudioCapture alloc] init];
        capture.outputPath = outputPath;
        capture.sampleRate = sampleRate;

        /* SIGTERM / SIGINT -> clean shutdown */
        signal(SIGTERM, SIG_IGN);
        signal(SIGINT, SIG_IGN);

        dispatch_source_t termSrc = dispatch_source_create(
            DISPATCH_SOURCE_TYPE_SIGNAL, SIGTERM, 0,
            dispatch_get_main_queue());
        dispatch_source_set_event_handler(termSrc, ^{
            [capture stop];
            exit(0);
        });
        dispatch_resume(termSrc);

        dispatch_source_t intSrc = dispatch_source_create(
            DISPATCH_SOURCE_TYPE_SIGNAL, SIGINT, 0,
            dispatch_get_main_queue());
        dispatch_source_set_event_handler(intSrc, ^{
            [capture stop];
            exit(0);
        });
        dispatch_resume(intSrc);

        /* Start capture */
        [capture startWithCompletion:^(BOOL success) {
            if (!success) exit(1);
        }];

        [[NSRunLoop mainRunLoop] run];
    }
    return 0;
}
