obj-m += cora_pwm.o
ccflags-y += -I$(src)/../include

module_src := $(shell pwd)

all:
	$(MAKE) -C $(KERNEL_SRC) M=$(module_src) O=$(O) modules

modules_install:
	$(MAKE) -C $(KERNEL_SRC) M=$(module_src) O=$(O) modules_install

clean:
	$(MAKE) -C $(KERNEL_SRC) M=$(module_src) O=$(O) clean
