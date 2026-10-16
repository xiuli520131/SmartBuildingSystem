#include <stdio.h>
#include <string.h>
#include <transform.h>
#include "door_app.h"

#if !defined(BOARD_EDU_RISCV64_EVB) || defined(SEPARATE_COMPILE)
#error "Smart access requires edu-riscv64 with combined compilation"
#endif
#ifdef USER_TEST_GPIO
#error "Disable Config test gpio with led and key before enabling smart access"
#endif

#define DOOR_LED_PIN 29
#define DOOR_UNLOCK_MS 3000
#define DOOR_HISTORY_SIZE 8
#define DOOR_ID_SIZE 13

enum DoorState { DOOR_LOCKED, DOOR_UNLOCKED, DOOR_FAULT };
enum DoorAction { DOOR_NONE, DOOR_ALLOW, DOOR_DENY };

struct DoorRequest {
    enum DoorAction action;
    char id[DOOR_ID_SIZE];
};

struct DoorRecord {
    struct DoorRequest request;
    int completed;
};

static pthread_mutex_t door_mutex;
static pthread_t door_thread;
static volatile int door_ready;
static int door_fd = -1;
static enum DoorState door_state = DOOR_FAULT;
static struct DoorRequest pending;
static struct DoorRecord history[DOOR_HISTORY_SIZE];
static unsigned int history_next;
static unsigned int manual_sequence;

static const char *StateName(void)
{
    if (door_state == DOOR_LOCKED) return "LOCKED";
    if (door_state == DOOR_UNLOCKED) return "UNLOCKED";
    return "FAULT";
}

static const char *ActionName(enum DoorAction action)
{
    return action == DOOR_ALLOW ? "ALLOW" : "DENY";
}

static int LedWrite(int unlocked)
{
    struct PinStat led = {0};
    led.pin = DOOR_LED_PIN;
    led.val = unlocked ? GPIO_LOW : GPIO_HIGH;
    return PrivWrite(door_fd, &led, 0) < 0 ? -1 : 0;
}

static int ValidId(const char *id)
{
    unsigned int i;
    if (strlen(id) != DOOR_ID_SIZE - 1) return 0;
    for (i = 0; i < DOOR_ID_SIZE - 1; ++i) {
        char c = id[i];
        if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f') ||
              (c >= 'A' && c <= 'F'))) return 0;
    }
    return 1;
}

static void NormalizeId(char *dest, const char *source)
{
    unsigned int i;
    for (i = 0; i < DOOR_ID_SIZE - 1; ++i) {
        char c = source[i];
        dest[i] = (c >= 'A' && c <= 'F') ? c + ('a' - 'A') : c;
    }
    dest[DOOR_ID_SIZE - 1] = '\0';
}

static void ErrorReply(const char *id, const char *reason)
{
    printf("\r\nAC|ERR|%s|%s\r\n", id, reason);
}

static void *DoorWorker(void *arg)
{
    (void)arg;
    for (;;) {
        struct DoorRequest request;
        unsigned int slot;

        PrivMutexObtain(&door_mutex);
        if (pending.action == DOOR_NONE) {
            PrivMutexAbandon(&door_mutex);
            PrivTaskDelay(20);
            continue;
        }
        request = pending;
        pending.action = DOOR_NONE;

        if (LedWrite(request.action == DOOR_ALLOW) != 0) {
            door_state = DOOR_FAULT;
            ErrorReply(request.id, "FAULT");
            PrivMutexAbandon(&door_mutex);
            continue;
        }

        door_state = request.action == DOOR_ALLOW ? DOOR_UNLOCKED : DOOR_LOCKED;
        slot = history_next;
        history_next = (history_next + 1) % DOOR_HISTORY_SIZE;
        history[slot].request = request;
        history[slot].completed = request.action == DOOR_DENY;
        printf("\r\nAC|ACK|%s|%s|OK\r\n", request.id, ActionName(request.action));
        PrivMutexAbandon(&door_mutex);

        if (request.action == DOOR_ALLOW) {
            // Only this worker changes the LED after initialization.
            PrivTaskDelay(DOOR_UNLOCK_MS);
            PrivMutexObtain(&door_mutex);
            if (LedWrite(0) != 0) {
                door_state = DOOR_FAULT;
                ErrorReply(request.id, "FAULT");
            } else {
                door_state = DOOR_LOCKED;
                history[slot].completed = 1;
                printf("\r\nAC|STATE|%s|LOCKED\r\n", request.id);
            }
            PrivMutexAbandon(&door_mutex);
        }
    }
    return NULL;
}

static int DoorCommand(int argc, char *argv[], int trusted)
{
    char id[DOOR_ID_SIZE] = "000000000000";
    enum DoorAction action;
    unsigned int i;

    if (!door_ready) {
        ErrorReply(id, "NOT_READY");
        return 0;
    }
    PrivMutexObtain(&door_mutex);
    if ((argc != 2 && argc != 3) || (argc == 3 && !ValidId(argv[2]))) {
        ErrorReply(id, "BAD_ARGS");
        printf("\r\nUsage: Door ping/status/allow/deny [12-hex-ID]\r\n");
        goto done;
    }
    if (argc == 3) {
        NormalizeId(id, argv[2]);
        // Reserve the ffff prefix for locally generated manual request IDs.
        if (strncmp(id, "ffff", 4) == 0) {
            ErrorReply(id, "BAD_ID");
            goto done;
        }
    } else {
        snprintf(id, sizeof(id), "ffff%08x", ++manual_sequence);
    }

    if (strcmp(argv[1], "ping") == 0) {
        printf("\r\nAC|PONG|%s|1\r\n", id);
        goto done;
    }
    if (strcmp(argv[1], "status") == 0) {
        printf("\r\nAC|STATUS|%s|%s\r\n", id, StateName());
        goto done;
    }
    if (strcmp(argv[1], "allow") == 0) action = DOOR_ALLOW;
    else if (strcmp(argv[1], "deny") == 0) action = DOOR_DENY;
    else {
        ErrorReply(id, "BAD_ARGS");
        goto done;
    }

#ifdef APP_ACCESS_FACE
    if (!trusted) {
        ErrorReply(id, "FACE_REQUIRED");
        goto done;
    }
#else
    (void)trusted;
#endif

    if (door_state == DOOR_FAULT) {
        ErrorReply(id, "FAULT");
        goto done;
    }
    for (i = 0; i < DOOR_HISTORY_SIZE; ++i) {
        if (history[i].request.action != DOOR_NONE &&
            strcmp(history[i].request.id, id) == 0) {
            if (history[i].request.action != action) ErrorReply(id, "BAD_ID");
            else {
                printf("\r\nAC|ACK|%s|%s|OK\r\n", id, ActionName(action));
                if (history[i].completed && action == DOOR_ALLOW)
                    printf("\r\nAC|STATE|%s|LOCKED\r\n", id);
            }
            goto done;
        }
    }
    if (pending.action != DOOR_NONE && strcmp(pending.id, id) == 0) {
        if (pending.action != action) ErrorReply(id, "BAD_ID");
        // The worker will send ACK once the queued GPIO action succeeds.
        goto done;
    }
    if (door_state != DOOR_LOCKED || pending.action != DOOR_NONE) {
        ErrorReply(id, "BUSY");
        goto done;
    }
    pending.action = action;
    memcpy(pending.id, id, sizeof(id));

done:
    PrivMutexAbandon(&door_mutex);
    return 0;
}

int Door(int argc, char *argv[])
{
    return DoorCommand(argc, argv, 0);
}

int DoorSubmitInternal(const char *request_id, int allow)
{
    char *args[] = {"Door", allow ? "allow" : "deny", (char *)request_id};
    return DoorCommand(3, args, 1);
}

PRIV_SHELL_CMD_FUNCTION(Door, smart access LED demo, PRIV_SHELL_CMD_MAIN_ATTR);

int DoorAppInit(void)
{
    struct PinParam pin = {0};
    struct PrivIoctlCfg cfg = {0};

    if (door_ready) return 0;
    if (PrivMutexCreate(&door_mutex, NULL) != 0) {
        printf("\r\nSmart access: mutex initialization failed\r\n");
        return -1;
    }
    door_fd = PrivOpen("/dev/pin_dev", O_RDWR);
    if (door_fd < 0) goto failed;
    pin.cmd = GPIO_CONFIG_MODE;
    pin.pin = DOOR_LED_PIN;
    pin.mode = GPIO_CFG_OUTPUT;
    cfg.ioctl_driver_type = PIN_TYPE;
    cfg.args = &pin;
    if (PrivIoctl(door_fd, OPE_CFG, &cfg) != 0 || LedWrite(0) != 0) goto failed;
    door_state = DOOR_LOCKED;

    // This XiZi pthread wrapper starts the task inside PrivTaskCreate.
    if (PrivTaskCreate(&door_thread, NULL, DoorWorker, NULL) != 0) goto failed;
    door_ready = 1;
    printf("\r\nSmart access ready: LED IO29, LOCKED, auto-lock 3000 ms\r\n");
    return 0;

failed:
    door_state = DOOR_FAULT;
    if (door_fd >= 0) {
        LedWrite(0);
        PrivClose(door_fd);
        door_fd = -1;
    }
    PrivMutexDelete(&door_mutex);
    printf("\r\nSmart access initialization failed; commands disabled\r\n");
    return -1;
}
