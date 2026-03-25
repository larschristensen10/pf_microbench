CC       = gcc
CFLAGS   = -O2 -march=native -Wall -Wextra -I common
LDFLAGS  =

COMMON_SRC = common/common.c
COMMON_HDR = common/timing.h common/memory.h common/calibrate.h

TARGETS = stream_tracker/stream_tracker \
          spatial_region/spatial_region \
          history_buffer/history_buffer \
          ip_table/ip_table

.PHONY: all clean

all: $(TARGETS)

stream_tracker/stream_tracker: stream_tracker/stream_tracker.c $(COMMON_SRC) $(COMMON_HDR)
	$(CC) $(CFLAGS) -o $@ stream_tracker/stream_tracker.c $(COMMON_SRC) $(LDFLAGS)

spatial_region/spatial_region: spatial_region/spatial_region.c $(COMMON_SRC) $(COMMON_HDR)
	$(CC) $(CFLAGS) -o $@ spatial_region/spatial_region.c $(COMMON_SRC) $(LDFLAGS)

history_buffer/history_buffer: history_buffer/history_buffer.c $(COMMON_SRC) $(COMMON_HDR)
	$(CC) $(CFLAGS) -o $@ history_buffer/history_buffer.c $(COMMON_SRC) $(LDFLAGS)

ip_table/ip_table: ip_table/ip_table.c $(COMMON_SRC) $(COMMON_HDR)
	$(CC) $(CFLAGS) -o $@ ip_table/ip_table.c $(COMMON_SRC) $(LDFLAGS)

clean:
	rm -f $(TARGETS)
