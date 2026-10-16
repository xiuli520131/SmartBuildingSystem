#ifndef ACCESS_FACE_FEATURE_H
#define ACCESS_FACE_FEATURE_H
#include <stdint.h>
#define FACE_SIDE 160
#define FACE_FEATURE_COUNT 4096
/* Input is an already cropped and resized uint8 grayscale ROI. */
void FaceEqualize(uint8_t *gray);
void FaceExtract(const uint8_t *gray, float *output);
uint32_t FaceCrc32(const uint8_t *data, unsigned int size);
#endif
