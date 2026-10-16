#ifndef SMART_ACCESS_DOOR_APP_H
#define SMART_ACCESS_DOOR_APP_H

int DoorAppInit(void);
int DoorSubmitInternal(const char *request_id, int allow);

#endif
