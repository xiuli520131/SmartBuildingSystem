/* Course prototype: board camera/detection/features, server identity/permission. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <math.h>
#include <transform.h>
#include <kpu.h>
#include <dvp.h>
#include <sysctl.h>
#include <clint.h>
#include "face_app.h"
#include "face_feature.h"
#include "region_layer.h"
#include "../access_control/door_app.h"

#if IMAGE_WIDTH != 320 || IMAGE_HEIGHT != 240
#error "The supplied face model requires camera resolution 320 x 240"
#endif
#ifdef USER_TEST_CAMERA
#error "Disable USER_TEST_CAMERA; Face owns the camera and KPU"
#endif

#define PIXELS (320 * 240)
#define LCD_WIDTH 320u
#define LCD_HEIGHT 320u
#define PREVIEW_Y 40u
#define REPLY_MS 8000u
#define STABLE_FRAMES 5
#define DRIFT_LIMIT 25.0f /* Candidate quality gate; tune on real, independent samples. */
extern const unsigned char access_face_model[];
extern const unsigned int access_face_model_size;
static uint16_t display[PIXELS] __attribute__((aligned(64)));
static uint8_t rgb[PIXELS * 3] __attribute__((aligned(64)));
static uint8_t gray[FACE_SIDE * FACE_SIDE];
static float feature[FACE_FEATURE_COUNT], waiting_feature[FACE_FEATURE_COUNT];
static float previous_feature[FACE_FEATURE_COUNT];
static kpu_model_context_t model;
static region_layer_t region;
static float anchors[] = {1.889f,2.5245f,2.9465f,3.94056f,3.99987f,
                         5.3658f,5.155437f,6.92275f,6.718375f,9.01025f};
static volatile uint32_t ai_done_flag;
static pthread_mutex_t face_mutex;
static pthread_mutex_t stage_mutex;
static pthread_t face_thread;
static uint64_t clock_hz;
static uint16_t lcd_line[LCD_WIDTH] __attribute__((aligned(64)));
static int lcd_requested, lcd_busy;
static volatile int initialized, hardware_ready, running, permanent_fault;
static int camera_fd = -1, lcd_fd = -1;
static char session[13], waiting_id[13], enrollment_id[13];
static uint32_t sequence, waiting_since, enrollment_since, cooldown_until;
static int waiting_mode, decision, enrolling, stable, released = 1, absent_frames;
static uint32_t frames, last_capture_ms, last_feature_ms;
static char health[32] = "INITIALIZING";
static obj_info_t previous_box;
/* SMART_ACCESS_CAPTURE_DIAG_V1: report the original failure, never auto-retry DMA. */
static const char * volatile capture_stage = "INITIALIZING";
static const char *capture_failure = "NONE";
static volatile uint32_t stage_since;
static uint32_t capture_since, camera_ms, kpu_ms, lcd_ms, lcd_row;

/* SMART_ACCESS_SHARED_CLOCK_V1: read mtime only; never reconfigure kernel timers.
 * The course demo keeps the CPU clock fixed after FaceAppInit. */
static uint32_t Now(void)
{
    uint64_t ticks=clint_get_time();
    return (uint32_t)((ticks/clock_hz)*1000ull +
                      ((ticks%clock_hz)*1000ull)/clock_hz);
}
static void CaptureStage(const char *name)
{
    PrivMutexObtain(&stage_mutex);
    stage_since=Now();
    capture_stage=name;
    PrivMutexAbandon(&stage_mutex);
}
static int CaptureFailure(const char *reason, uint32_t detail)
{
    uint32_t now=Now();
    capture_failure=reason;
    printf("\r\nFace fault reason=%s stage=%s frames=%u detail=0x%08x "
           "total=%ums stage_ms=%u camera=%ums kpu=%ums lcd=%ums row=%u "
           "dvp_sts=0x%08x dvp_cfg=0x%08x layer=%u/%u\r\n",
           reason,capture_stage,(unsigned int)frames,(unsigned int)detail,
           (unsigned int)(now-capture_since),(unsigned int)(now-stage_since),
           (unsigned int)camera_ms,(unsigned int)kpu_ms,(unsigned int)lcd_ms,
           (unsigned int)lcd_row,(unsigned int)dvp->sts,(unsigned int)dvp->dvp_cfg,
           (unsigned int)model.current_layer,(unsigned int)model.layers_length);
    CaptureStage("FAULT");
    return -1;
}
/* Sample the CPU-visible buffers. These numbers alone do not validate an image. */
static void SampleBuffers(void)
{
    unsigned int c,i;
    printf("\r\nFace sample frame=%u",(unsigned int)frames);
    for (c=0;c<3;++c) {
        unsigned int low=255,high=0,total=0,hash=2166136261u;
        const volatile uint8_t *channel=rgb+c*PIXELS;
        for (i=0;i<256;++i) {
            unsigned int value=channel[i*(PIXELS-1)/255];
            if (value<low) low=value;
            if (value>high) high=value;
            total+=value;
            hash=(hash^value)*16777619u;
        }
        printf(" %c=%u/%u/%u/%08x","RGB"[c],low,high,total/256,hash);
    }
    {
        unsigned int low=65535,high=0,hash=2166136261u;
        const volatile uint16_t *pixels=display;
        for (i=0;i<256;++i) {
            unsigned int value=pixels[i*(PIXELS-1)/255];
            if (value<low) low=value;
            if (value>high) high=value;
            hash=(hash^value)*16777619u;
        }
        printf(" RGB565=%04x/%04x/%08x\r\n",low,high,hash);
    }
}
static int ValidId(const char *id)
{
    unsigned int i;
    if (!id || strlen(id) != 12 || !strncmp(id,"ffff",4)) return 0;
    for (i=0;i<12;++i) if (!((id[i]>='0'&&id[i]<='9') || (id[i]>='a'&&id[i]<='f'))) return 0;
    return 1;
}
static void ReplyError(const char *id, const char *why)
{ printf("\r\nAC|ERR|%s|%s\r\n", id, why); }
/* Called with face_mutex held. Cancellation can never enqueue a lock action. */
static void Cancel(const char *reason)
{
    if (waiting_mode) printf("\r\nAC|FCANCEL|%s|%s\r\n", waiting_id, reason);
    if (enrolling && (!waiting_mode || strcmp(enrollment_id,waiting_id)))
        printf("\r\nAC|FCANCEL|%s|%s\r\n", enrollment_id, reason);
    waiting_mode=decision=enrolling=stable=0;
    waiting_id[0]=enrollment_id[0]=0;
}
static float FeatureDistance(const float *a, const float *b)
{
    unsigned int i;
    float sum=0;
    for (i=0;i<FACE_FEATURE_COUNT;++i) {
        float total=a[i]+b[i], diff=a[i]-b[i];
        if (total>0) sum += 2*diff*diff/total;
    }
    return sum;
}
static int SameBox(const obj_info_t *a, const obj_info_t *b)
{
    int left=(int)(a->obj[0].x1>b->obj[0].x1?a->obj[0].x1:b->obj[0].x1);
    int top=(int)(a->obj[0].y1>b->obj[0].y1?a->obj[0].y1:b->obj[0].y1);
    int right=(int)(a->obj[0].x2<b->obj[0].x2?a->obj[0].x2:b->obj[0].x2);
    int bottom=(int)(a->obj[0].y2<b->obj[0].y2?a->obj[0].y2:b->obj[0].y2);
    int area_a=(a->obj[0].x2-a->obj[0].x1)*(a->obj[0].y2-a->obj[0].y1);
    int area_b=(b->obj[0].x2-b->obj[0].x1)*(b->obj[0].y2-b->obj[0].y1);
    int intersection=(right>left&&bottom>top)?(right-left)*(bottom-top):0;
    return intersection*10 > (area_a+area_b-intersection)*5;
}
static int CameraControl(int op, void *args)
{
    struct PrivIoctlCfg cfg={0};
    cfg.ioctl_driver_type=CAMERA_TYPE;
    cfg.args=args;
    return PrivIoctl(camera_fd,op,&cfg);
}
static void AiDone(void *arg) { (void)arg; ai_done_flag=1; }

static int HardwareInit(void)
{
    struct CameraCfg cfg={.window_w=800,.window_h=600,.output_w=320,.output_h=240,
                          .gain_manu_enable=0,.gain=0xff};
    RgbAddress addresses={(uintptr_t)rgb,(uintptr_t)(rgb+PIXELS),(uintptr_t)(rgb+2*PIXELS)};
    sysctl_clock_enable(SYSCTL_CLOCK_AI);
    if (access_face_model_size != 388776u || kpu_load_kmodel(&model,access_face_model)) return -1;
    region.anchor_number=5;
    region.anchor=anchors;
    region.nms_value=0.3f;
    if (region_layer_init(&region,20,15,30,320,240)) return -1;
    region.threshold[0]=0.7f;
    camera_fd=PrivOpen("/dev/camera_dev",O_RDWR);
    if (camera_fd<0 || CameraControl(OPE_CFG,&cfg) || CameraControl(SET_AI_ADDR,&addresses)) return -1;
    lcd_fd=PrivOpen("/dev/lcd_dev",O_RDWR);
    /* Camera/feature operation can continue if the optional display is absent. */
    return 0;
}
/* SMART_ACCESS_LCD_PATTERN_V1: the worker alone owns all LCD writes.
 * Use the same row-write API as preview, without camera pixels or KPU output. */
static int LcdRow(unsigned int row, uint16_t *pixels)
{
    LcdWriteParam write={0};
    int result;
    write.type=LCD_DOT_TYPE;
    write.pixel_info.pixel_color=pixels;
    write.pixel_info.x_startpos=0; write.pixel_info.x_endpos=LCD_WIDTH-1;
    write.pixel_info.y_startpos=row; write.pixel_info.y_endpos=row;
    lcd_row=row;
    result=PrivWrite(lcd_fd,&write,0);
    return result<0?CaptureFailure("LCD_WRITE_FAILED",(uint32_t)result):0;
}
static int DisplayPattern(int pattern)
{
    static const uint16_t colors[]={0xf800,0x07e0,0x001f,0xffff,0x0000,0x8410};
    unsigned int x,y;
    if (lcd_fd<0) return 0;
    for (x=0;x<LCD_WIDTH;++x)
        lcd_line[x]=pattern?colors[x*6u/LCD_WIDTH]:0;
    for (y=0;y<LCD_HEIGHT;++y) {
        if (LcdRow(y,lcd_line)) return -1;
        if (y%16u==0) PrivTaskDelay(1);
    }
    return 0;
}
static int DisplayFrame(const obj_info_t *boxes)
{
    unsigned int i,x;
    if (lcd_fd<0) return 0;
    for (i=0;i<boxes->obj_number;++i) draw_edge((uint32_t *)display,(obj_info_t *)boxes,i,0xf800,320,240);
    /* SMART_ACCESS_PREVIEW_ROTATE_180_V1: the confirmed landscape mounting
     * detects faces, while this LCD orientation displays them upside down.
     * Rotate only the RGB565 preview (including boxes); keep model/feature RGB
     * and box coordinates in the proven camera orientation. */
    for (i=0;i<240;++i) {
        const uint16_t *source=display+LCD_WIDTH*(239u-i);
        for (x=0;x<LCD_WIDTH;++x) lcd_line[x]=source[LCD_WIDTH-1u-x];
        if (LcdRow(i+PREVIEW_Y,lcd_line)) return -1;
    }
    return 0;
}
/* All buffers remain static on timeout: an active DMA must never reuse freed memory. */
static int Capture(obj_info_t *boxes)
{
    _ioctl_shoot_para shot={(uintptr_t)display,sizeof(display)};
    uint32_t start=Now();
    int flag=2;
    float *output=NULL;
    size_t size=0;
    unsigned int i;
    int result;
    capture_since=start;
    camera_ms=kpu_ms=lcd_ms=lcd_row=0;
    capture_failure="NONE";
    CaptureStage("CAMERA_START");
    result=CameraControl(IOCTRL_CAMERA_START_SHOT,&shot);
    if (result) return CaptureFailure("CAMERA_START_FAILED",(uint32_t)result);
    CaptureStage("WAIT_CAMERA");
    do {
        result=CameraControl(FLAG_CHECK,&flag);
        if (result) return CaptureFailure("CAMERA_FLAG_FAILED",(uint32_t)result);
        if ((uint32_t)(Now()-start)>1200u) return CaptureFailure("CAMERA_WAIT_TIMEOUT",(uint32_t)flag);
        if (flag) PrivTaskDelay(2);
    } while (flag);
    __sync_synchronize();
    camera_ms=Now()-start;
    ai_done_flag=0;
    CaptureStage("KPU_START");
    result=kpu_run_kmodel(&model,rgb,(dmac_channel_number_t)KPU_DMA_CH,AiDone,NULL);
    if (result) return CaptureFailure("KPU_START_FAILED",(uint32_t)result);
    CaptureStage("WAIT_KPU");
    while (!ai_done_flag) {
        if ((uint32_t)(Now()-start)>2200u) return CaptureFailure("KPU_WAIT_TIMEOUT",ai_done_flag);
        PrivTaskDelay(2);
    }
    __sync_synchronize();
    kpu_ms=Now()-start-camera_ms;
    CaptureStage("KPU_OUTPUT");
    result=kpu_get_output(&model,0,(uint8_t **)&output,&size);
    if (result) return CaptureFailure("KPU_OUTPUT_FAILED",(uint32_t)result);
    if (!output || size!=20u*15u*30u*sizeof(float)) return CaptureFailure("KPU_OUTPUT_INVALID",(uint32_t)size);
    for (i=0;i<size/sizeof(float);++i)
        if (!isfinite(output[i])) return CaptureFailure("KPU_OUTPUT_NONFINITE",i);
    region.input=output;
    memset(boxes,0,sizeof(*boxes));
    CaptureStage("POSTPROCESS");
    region_layer_run(&region,boxes);
    last_capture_ms=Now()-start;
    CaptureStage("LCD_WRITE");
    if (DisplayFrame(boxes)) return -1;
    lcd_ms=Now()-stage_since;
    ++frames;
    CaptureStage("FRAME_DONE");
    return 0;
}
static int Extract(const obj_info_t *boxes)
{
    unsigned int x,y,width,height,x1,y1,clipped=0,total=0,gradient=0;
    uint32_t start=Now();
    if (boxes->obj_number!=1) return 0;
    x1=boxes->obj[0].x1; y1=boxes->obj[0].y1;
    if (x1>=320 || y1>=240 || boxes->obj[0].x2<=x1 || boxes->obj[0].y2<=y1) return 0;
    width=boxes->obj[0].x2-x1; height=boxes->obj[0].y2-y1;
    if (width<60 || height<60 || x1+width>320 || y1+height>240) return 0;
    for (y=0;y<FACE_SIDE;++y) for (x=0;x<FACE_SIDE;++x) {
        unsigned int pixel=(y1+y*height/FACE_SIDE)*320+x1+x*width/FACE_SIDE;
        uint8_t value=(77u*rgb[pixel]+150u*rgb[pixel+PIXELS]+29u*rgb[pixel+2*PIXELS])>>8;
        gray[y*FACE_SIDE+x]=value;
        total+=value;
        if (value<10 || value>245) ++clipped;
        if (x) gradient+=abs((int)value-(int)gray[y*FACE_SIDE+x-1]);
    }
    if (total<35u*FACE_SIDE*FACE_SIDE || total>220u*FACE_SIDE*FACE_SIDE ||
        clipped>FACE_SIDE*FACE_SIDE/3 || gradient<2u*FACE_SIDE*(FACE_SIDE-1)) return 0;
    FaceEqualize(gray);
    FaceExtract(gray,feature);
    last_feature_ms=Now()-start;
    return 1;
}
static void Encode64(const uint8_t *src,unsigned int length,char *out)
{
    const char *alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    unsigned int i,pos=0;
    for (i=0;i<length;i+=3) {
        uint32_t word=(uint32_t)src[i]<<16;
        if (i+1<length) word|=(uint32_t)src[i+1]<<8;
        if (i+2<length) word|=src[i+2];
        out[pos++]=alphabet[(word>>18)&63]; out[pos++]=alphabet[(word>>12)&63];
        out[pos++]=i+1<length?alphabet[(word>>6)&63]:'=';
        out[pos++]=i+2<length?alphabet[word&63]:'=';
    }
    out[pos]=0;
}
static int Upload(const char *id,const char *token,int mode)
{
    char encoded[685];
    const uint8_t *data=(const uint8_t *)waiting_feature;
    unsigned int offset;
    printf("\r\nAC|FBEGIN|%s|%s|lbph4-v1|16384|%08x|%s\r\n",id,token,
           (unsigned int)FaceCrc32(data,16384),mode==2?"ENROLL":"INFER");
    for (offset=0;offset<16384;offset+=512) {
        PrivMutexObtain(&face_mutex);
        if ((uint32_t)(Now()-waiting_since)>REPLY_MS) Cancel("TRANSFER_TIMEOUT");
        if (!running || !waiting_mode || strcmp(id,waiting_id) || strcmp(token,session)) {
            PrivMutexAbandon(&face_mutex); return -1;
        }
        PrivMutexAbandon(&face_mutex);
        Encode64(data+offset,512,encoded);
        /* A complete chunk is one printf; CRC rejects interrupted/corrupted lines. */
        printf("\r\nAC|FCHUNK|%s|%u|%s\r\n",id,offset,encoded);
        PrivTaskDelay(1);
    }
    printf("\r\nAC|FEND|%s|%s\r\n",id,token);
    return 0;
}
static void NextId(char *dest)
{
    uint64_t value=strtoull(session,NULL,16);
    do { value=(strtoull(session,NULL,16)+(uint64_t)++sequence)&0xffffffffffffull;
         snprintf(dest,13,"%012llx",(unsigned long long)value);
    } while (!ValidId(dest) || !strcmp(dest,session));
}
static void *FaceWorker(void *arg)
{
    obj_info_t boxes;
    int preview_cleared=0;
    (void)arg;
    if (HardwareInit()) {
        PrivMutexObtain(&face_mutex);
        permanent_fault=1; strcpy(health,"HARDWARE_INIT_FAILED");
        PrivMutexAbandon(&face_mutex);
        printf("\r\nFace: hardware/model initialization failed; reboot after fixing config\r\n");
        CaptureStage("INIT_FAILED");
        return NULL;
    }
    PrivMutexObtain(&face_mutex);
    hardware_ready=1; strcpy(health,"STOPPED");
    PrivMutexAbandon(&face_mutex);
    CaptureStage("IDLE");
    printf("\r\nFace: camera/KPU initialized; diagnostics=v2.3.1; preview=ROT180; clock=CLINT/%lluHz; "
           "Face lcd for color bars; Face start for preview\r\n",(unsigned long long)clock_hz);
    for (;;) {
        int active,good,pattern;
        char upload_id[13],upload_session[13];
        int upload_mode=0;
        PrivMutexObtain(&face_mutex);
        active=running; pattern=lcd_requested;
        if (pattern) { lcd_requested=0; lcd_busy=1; strcpy(health,"LCD_PATTERN"); }
        PrivMutexAbandon(&face_mutex);
        if (pattern) {
            int result;
            capture_since=Now();
            CaptureStage("LCD_PATTERN");
            result=DisplayPattern(1);
            preview_cleared=0;
            PrivMutexObtain(&face_mutex);
            lcd_busy=0;
            if (result) {
                Cancel("CAPTURE_FAULT"); running=0; permanent_fault=1;
                snprintf(health,sizeof(health),"%s",capture_failure);
            } else strcpy(health,"LCD_PATTERN_DONE");
            PrivMutexAbandon(&face_mutex);
            if (result) return NULL;
            CaptureStage("IDLE");
            printf("\r\nFace lcd: %s; bars=RED,GREEN,BLUE,WHITE,BLACK,GRAY; frames=%u\r\n",
                   lcd_fd<0?"NO_LCD":"DONE",(unsigned int)frames);
            continue;
        }
        if (!active) { PrivTaskDelay(50); continue; }
        good=0;
        if (!preview_cleared) {
            capture_since=Now();
            CaptureStage("LCD_CLEAR");
            good=DisplayPattern(0);
            preview_cleared=!good;
        }
        if (good || Capture(&boxes)) {
            PrivMutexObtain(&face_mutex);
            Cancel("CAPTURE_FAULT"); running=0; permanent_fault=1;
            snprintf(health,sizeof(health),"%s",capture_failure);
            PrivMutexAbandon(&face_mutex);
            printf("\r\nFace: capture worker stopped; reason=%s; reset required\r\n",capture_failure);
            return NULL;
        }
        CaptureStage("FEATURE");
        good=Extract(&boxes);
        PrivMutexObtain(&face_mutex);
        if (!running) { PrivMutexAbandon(&face_mutex); continue; }
        strcpy(health,boxes.obj_number==0?"NO_FACE":boxes.obj_number>1?"MULTIPLE_FACES":good?"FACE_STABLE":"QUALITY_REJECTED");
        if (enrolling && (uint32_t)(Now()-enrollment_since)>30000u) Cancel("ENROLL_TIMEOUT");
        if (waiting_mode && (uint32_t)(Now()-waiting_since)>REPLY_MS) Cancel("REPLY_TIMEOUT");
        if (!good) {
            int sample_due=!session[0] && (frames==1 || frames%30u==0);
            stable=0;
            if (waiting_mode) Cancel("FACE_CHANGED");
            if (!boxes.obj_number) { if (++absent_frames>=3) released=1; }
            else absent_frames=0;
            PrivMutexAbandon(&face_mutex);
            if (sample_due) SampleBuffers();
            CaptureStage("IDLE"); PrivTaskDelay(50); continue;
        }
        absent_frames=0;
        if (waiting_mode) {
            if (FeatureDistance(feature,waiting_feature)>DRIFT_LIMIT) Cancel("FACE_CHANGED");
            else if (waiting_mode==1 && decision) {
                /* This decision follows a fresh captured frame, never an old upload buffer. */
                int allow=decision==1;
                char id[13]; strcpy(id,waiting_id);
                waiting_mode=decision=0; waiting_id[0]=0;
                cooldown_until=Now()+3000u;
                DoorSubmitInternal(id,allow);
            }
            PrivMutexAbandon(&face_mutex); PrivTaskDelay(50); continue;
        }
        if (!stable || !SameBox(&boxes,&previous_box) || FeatureDistance(feature,previous_feature)>DRIFT_LIMIT) stable=1;
        else ++stable;
        previous_box=boxes;
        memcpy(previous_feature,feature,sizeof(feature));
        if (stable>=STABLE_FRAMES && session[0] && (enrolling || released) &&
            (int32_t)(Now()-cooldown_until)>=0) {
            if (enrolling) { strcpy(waiting_id,enrollment_id); waiting_mode=2; }
            else { NextId(waiting_id); waiting_mode=1; }
            waiting_since=Now(); decision=0; stable=0; released=0;
            memcpy(waiting_feature,feature,sizeof(feature));
            strcpy(upload_id,waiting_id); strcpy(upload_session,session); upload_mode=waiting_mode;
        }
        PrivMutexAbandon(&face_mutex);
        if (upload_mode) Upload(upload_id,upload_session,upload_mode);
        CaptureStage("IDLE");
        PrivTaskDelay(50);
    }
    return NULL;
}

int FaceHello(int argc,char *argv[])
{
    if (argc!=2 || !ValidId(argv[1])) return 0;
    if (!initialized) { ReplyError(argv[1],"NOT_READY"); return 0; }
    PrivMutexObtain(&face_mutex);
    if (!hardware_ready || permanent_fault) ReplyError(argv[1],"NOT_READY");
    else if (lcd_requested || lcd_busy) ReplyError(argv[1],"BUSY");
    else {
        if (strcmp(session,argv[1])) { Cancel("SESSION_CHANGED"); strcpy(session,argv[1]); sequence=0; }
        running=1;
        printf("\r\nAC|FREADY|%s|lbph4-v1\r\n",session);
    }
    PrivMutexAbandon(&face_mutex); return 0;
}
int FaceProbe(int argc,char *argv[])
{
    if (argc!=2 || !ValidId(argv[1])) return 0;
    if (!initialized) { ReplyError(argv[1],"NOT_READY"); return 0; }
    PrivMutexObtain(&face_mutex);
    printf("\r\nAC|FPROBE|%s|%s|%d|%s\r\n",argv[1],session[0]?session:"NONE",running,health);
    PrivMutexAbandon(&face_mutex); return 0;
}
int FaceEnroll(int argc,char *argv[])
{
    if (argc!=2 || !ValidId(argv[1])) return 0;
    if (!initialized) { ReplyError(argv[1],"NOT_READY"); return 0; }
    PrivMutexObtain(&face_mutex);
    if (!hardware_ready || !running || !session[0] || permanent_fault) ReplyError(argv[1],"NOT_READY");
    else if (waiting_mode || enrolling) ReplyError(argv[1],"BUSY");
    else {
        enrolling=1; stable=0; enrollment_since=Now(); strcpy(enrollment_id,argv[1]);
        printf("\r\nAC|FENROLL|%s|OK\r\n",argv[1]);
    }
    PrivMutexAbandon(&face_mutex); return 0;
}
int FaceSaved(int argc,char *argv[])
{
    if (argc!=2 || !ValidId(argv[1]) || !initialized) return 0;
    PrivMutexObtain(&face_mutex);
    if (waiting_mode==2 && !strcmp(waiting_id,argv[1]) && (uint32_t)(Now()-waiting_since)<REPLY_MS) {
        waiting_mode=enrolling=decision=0; waiting_id[0]=enrollment_id[0]=0;
        printf("\r\nAC|FSAVED|%s|OK\r\n",argv[1]);
    } else ReplyError(argv[1],"STALE_REQUEST");
    PrivMutexAbandon(&face_mutex); return 0;
}
int FaceDecision(int argc,char *argv[])
{
    if (argc!=3 || !ValidId(argv[1]) || !initialized) return 0;
    PrivMutexObtain(&face_mutex);
    if (strcmp(argv[2],"ALLOW") && strcmp(argv[2],"DENY")) ReplyError(argv[1],"BAD_ARGS");
    else if (!running || waiting_mode!=1 || strcmp(waiting_id,argv[1]) || decision ||
             (uint32_t)(Now()-waiting_since)>=REPLY_MS) ReplyError(argv[1],"STALE_REQUEST");
    else decision=!strcmp(argv[2],"ALLOW")?1:2;
    /* Worker confirms via ACK only after checking the next completed camera frame. */
    PrivMutexAbandon(&face_mutex); return 0;
}
int Face(int argc,char *argv[])
{
    const char *stage_name;
    uint32_t stage_age;
    if (!initialized) { printf("Face not initialized\r\n"); return 0; }
    PrivMutexObtain(&face_mutex);
    if (argc==2 && !strcmp(argv[1],"start") && hardware_ready && !permanent_fault) {
        if (lcd_requested || lcd_busy) printf("Face: LCD busy; wait for Face lcd: DONE\r\n");
        else running=1;
    }
    else if (argc==2 && !strcmp(argv[1],"stop")) { running=0; Cancel("STOPPED"); session[0]=0; strcpy(health,"STOPPED"); }
    else if (argc==2 && !strcmp(argv[1],"lcd")) {
        if (!hardware_ready || permanent_fault) printf("Face: not ready; reset required after fault\r\n");
        else if (running || session[0] || lcd_requested || lcd_busy)
            printf("Face: stop backend, then Face stop; wait before Face lcd\r\n");
        else if (lcd_fd<0) printf("Face: LCD unavailable\r\n");
        else { lcd_requested=1; strcpy(health,"LCD_QUEUED"); }
    }
    /* Read the stage/time pair and then the clock under the same lock.
     * A printf argument must not read a future timestamp from another core. */
    PrivMutexObtain(&stage_mutex);
    stage_name=capture_stage;
    stage_age=Now()-stage_since;
    PrivMutexAbandon(&stage_mutex);
    printf("\r\nFace ready=%d running=%d state=%s frames=%u capture=%ums feature=%ums\r\n",
           hardware_ready&&!permanent_fault,running,health,(unsigned int)frames,
           (unsigned int)last_capture_ms,(unsigned int)last_feature_ms);
    printf("Face diag=v2.3.1 preview=ROT180 clock=CLINT stage=%s stage_ms=%u error=%s camera=%ums kpu=%ums lcd=%ums row=%u\r\n",
           stage_name,(unsigned int)stage_age,capture_failure,
           (unsigned int)camera_ms,(unsigned int)kpu_ms,(unsigned int)lcd_ms,(unsigned int)lcd_row);
    PrivMutexAbandon(&face_mutex); return 0;
}
PRIV_SHELL_CMD_FUNCTION(Face, preview status or lcd bars, PRIV_SHELL_CMD_MAIN_ATTR);
PRIV_SHELL_CMD_FUNCTION(FaceHello, bind feature session, PRIV_SHELL_CMD_MAIN_ATTR);
PRIV_SHELL_CMD_FUNCTION(FaceProbe, query feature session, PRIV_SHELL_CMD_MAIN_ATTR);
PRIV_SHELL_CMD_FUNCTION(FaceEnroll, capture enrollment feature, PRIV_SHELL_CMD_MAIN_ATTR);
PRIV_SHELL_CMD_FUNCTION(FaceSaved, acknowledge stored enrollment, PRIV_SHELL_CMD_MAIN_ATTR);
PRIV_SHELL_CMD_FUNCTION(FaceDecision, reply to current face request, PRIV_SHELL_CMD_MAIN_ATTR);

int FaceAppInit(void)
{
    pthread_attr_t attributes = {0};
    struct sched_param priority = {0};
    int result;
    if (initialized) return 0;
    clock_hz=(uint64_t)SysctlClockGetFreq(SYSCTL_CLOCK_CPU)/CLINT_CLOCK_DIV;
    if (!clock_hz) return -1;
    if (PrivMutexCreate(&face_mutex,NULL)) return -1;
    if (PrivMutexCreate(&stage_mutex,NULL)) { PrivMutexDelete(&face_mutex); return -1; }
    stage_since=Now();
    pthread_attr_init(&attributes);
    /* XiZi's attr_init is a no-op; explicitly initialize the task priority. */
    priority.sched_priority = 11;
    pthread_attr_setschedparam(&attributes,&priority);
    pthread_attr_setstacksize(&attributes,16*1024);
    initialized=1;
    result=PrivTaskCreate(&face_thread,&attributes,FaceWorker,NULL);
    /* This XiZi subset has no attr_destroy and allocates no attribute resources. */
    if (result) { initialized=0; PrivMutexDelete(&stage_mutex); PrivMutexDelete(&face_mutex); return -1; }
    return 0;
}
