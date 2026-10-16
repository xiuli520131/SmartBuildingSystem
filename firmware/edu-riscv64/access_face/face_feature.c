#include "face_feature.h"
#include <float.h>
#include <math.h>
#include <string.h>

void FaceEqualize(uint8_t *gray)
{
    unsigned int bins[256] = {0}, cumulative = 0;
    unsigned int i, first = 0, denominator;
    uint8_t lut[256];
    for (i = 0; i < FACE_SIDE * FACE_SIDE; ++i) ++bins[gray[i]];
    while (first < 255 && !bins[first]) ++first;
    denominator = FACE_SIDE * FACE_SIDE - bins[first];
    if (!denominator) return;
    for (i = 0; i < 256; ++i) {
        cumulative += bins[i];
        lut[i] = cumulative <= bins[first] ? 0 :
            (uint8_t)(((cumulative - bins[first]) * 255 + denominator / 2) / denominator);
    }
    for (i = 0; i < FACE_SIDE * FACE_SIDE; ++i) gray[i] = lut[gray[i]];
}

void FaceExtract(const uint8_t *gray, float *output)
{
    int fx[8], fy[8], cx[8], cy[8];
    float w1[8], w2[8], w3[8], w4[8];
    int n, gx, gy, x, y;
    /* OpenCV ELBP uses float coordinates and bilinear interpolation. */
    for (n = 0; n < 8; ++n) {
        float px = (float)cos(2.0 * 3.14159265358979323846 * n / 8);
        float py = (float)-sin(2.0 * 3.14159265358979323846 * n / 8);
        float tx, ty;
        fx[n] = (int)floor(px); fy[n] = (int)floor(py);
        cx[n] = (int)ceil(px); cy[n] = (int)ceil(py);
        tx = px - fx[n]; ty = py - fy[n];
        w1[n] = (1-tx)*(1-ty); w2[n] = tx*(1-ty);
        w3[n] = (1-tx)*ty; w4[n] = tx*ty;
    }
    memset(output, 0, FACE_FEATURE_COUNT * sizeof(float));
    /* ELBP 158x158 / 4 gives 39-pixel cells; skip the trailing 2 pixels. */
    for (gy = 0; gy < 4; ++gy) for (gx = 0; gx < 4; ++gx) {
        float *histogram = output + (gy * 4 + gx) * 256;
        for (y = gy * 39 + 1; y < gy * 39 + 40; ++y)
            for (x = gx * 39 + 1; x < gx * 39 + 40; ++x) {
                unsigned int code = 0;
                float center = gray[y * FACE_SIDE + x];
                for (n = 0; n < 8; ++n) {
                    float sample = w1[n] * gray[(y+fy[n])*FACE_SIDE+x+fx[n]]
                                 + w2[n] * gray[(y+fy[n])*FACE_SIDE+x+cx[n]]
                                 + w3[n] * gray[(y+cy[n])*FACE_SIDE+x+fx[n]]
                                 + w4[n] * gray[(y+cy[n])*FACE_SIDE+x+cx[n]];
                    if (sample > center || fabsf(sample-center) < FLT_EPSILON) code |= 1u << n;
                }
                histogram[code] += 1.0f;
            }
        for (n = 0; n < 256; ++n) histogram[n] /= 1521.0f;
    }
}

uint32_t FaceCrc32(const uint8_t *data, unsigned int size)
{
    uint32_t crc = 0xffffffffu;
    unsigned int i, bit;
    for (i = 0; i < size; ++i) {
        crc ^= data[i];
        for (bit = 0; bit < 8; ++bit) crc = (crc >> 1) ^ (0xedb88320u & (0u-(crc&1u)));
    }
    return crc ^ 0xffffffffu;
}
