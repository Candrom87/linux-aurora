// SPDX-License-Identifier: GPL-2.0-only OR MIT
/*
 * Apple SoC PMGR device power state driver
 *
 * Copyright The Asahi Linux Contributors
 */

#include <linux/bitops.h>
#include <linux/bitfield.h>
#include <linux/err.h>
#include <linux/init.h>
#include <linux/kstrtox.h>
#include <linux/of.h>
#include <linux/of_address.h>
#include <linux/platform_device.h>
#include <linux/pm_domain.h>
#include <linux/regmap.h>
#include <linux/mfd/syscon.h>
#include <linux/reset-controller.h>
#include <linux/module.h>

#define APPLE_PMGR_RESET        BIT(31)
#define APPLE_PMGR_AUTO_ENABLE  BIT(28)
#define APPLE_PMGR_PS_AUTO      GENMASK(27, 24)
#define APPLE_PMGR_PS_MIN       GENMASK(19, 16)
#define APPLE_PMGR_PS_RESET     BIT(12)
#define APPLE_PMGR_BUSY         BIT(11)
#define APPLE_PMGR_DEV_DISABLE  BIT(10)
#define APPLE_PMGR_WAS_CLKGATED BIT(9)
#define APPLE_PMGR_WAS_PWRGATED BIT(8)
#define APPLE_PMGR_PS_ACTUAL    GENMASK(7, 4)
#define APPLE_PMGR_PS_TARGET    GENMASK(3, 0)

#define APPLE_PMGR_FLAGS        (APPLE_PMGR_WAS_CLKGATED | APPLE_PMGR_WAS_PWRGATED)

#define APPLE_PMGR_PS_ACTIVE    0xf
#define APPLE_PMGR_PS_CLKGATE   0x4
#define APPLE_PMGR_PS_PWRGATE   0x0

#define APPLE_PMGR_PS_SET_TIMEOUT 100
#define APPLE_PMGR_RESET_TIME 1

#if IS_ENABLED(CONFIG_APPLE_T6034_GPU_DIAG_NO_AUTO)
/* Experimental one-shot test kernel only: never enabled without the option. */
static bool t6034_gpu_diag_no_auto;
static bool t6034_gpu_diag_afr_hold;

static int __init t6034_gpu_diag_setup(char *arg)
{
	return kstrtobool(arg, &t6034_gpu_diag_no_auto);
}
early_param("apple_t6034_gpu_diag.no_auto", t6034_gpu_diag_setup);

static int __init t6034_gpu_diag_afr_setup(char *arg)
{
	return kstrtobool(arg, &t6034_gpu_diag_afr_hold);
}
early_param("apple_t6034_gpu_diag.afr_hold", t6034_gpu_diag_afr_setup);

static bool t6034_gpu_diag_match(bool opted, bool board, bool soc, bool compatible,
				resource_size_t base, resource_size_t size, u32 offset)
{
	return opted && board && soc && compatible && base == 0x408e80000ULL &&
	       size == 0x4000 && offset == 0x110;
}

static bool t6034_gpu_diag_domain(struct device_node *node, u32 offset)
{
	struct device_node *parent;
	struct resource res;
	bool match = false;

	if (!t6034_gpu_diag_no_auto || !of_machine_is_compatible("apple,j514m") ||
	    !of_machine_is_compatible("apple,t6034") ||
	    !of_device_is_compatible(node, "apple,t6031-pmgr-pwrstate"))
		return false;
	parent = of_get_parent(node);
	if (parent && of_device_is_compatible(parent, "apple,t6031-pmgr") &&
	    !of_address_to_resource(parent, 0, &res))
		match = t6034_gpu_diag_match(true, true, true, true, res.start,
					    resource_size(&res), offset);
	of_node_put(parent);
	return match;
}

static bool t6034_gpu_diag_afr_match(bool gpu_opted, bool afr_opted, bool board,
				    bool soc, bool compatible, bool always_on,
				    bool firmware_owned, resource_size_t base,
				    resource_size_t size, u32 offset)
{
	return gpu_opted && afr_opted && board && soc && compatible && always_on &&
	       !firmware_owned && base == 0x408e80000ULL && size == 0x4000 &&
	       offset == 0x100;
}

static bool t6034_gpu_diag_afr_domain(struct device_node *node, u32 offset)
{
	struct device_node *parent;
	struct resource res;
	u32 reg[2];
	bool match = false;

	if (!t6034_gpu_diag_no_auto || !t6034_gpu_diag_afr_hold ||
	    !of_machine_is_compatible("apple,j514m") ||
	    !of_machine_is_compatible("apple,t6034") ||
	    !of_device_is_compatible(node, "apple,t6031-pmgr-pwrstate") ||
	    of_property_count_u32_elems(node, "reg") != 2 ||
	    of_property_read_u32_array(node, "reg", reg, 2) ||
	    reg[0] != offset || reg[1] != 4)
		return false;
	parent = of_get_parent(node);
	if (parent && of_device_is_compatible(parent, "apple,t6031-pmgr") &&
	    !of_address_to_resource(parent, 0, &res))
		match = t6034_gpu_diag_afr_match(true, true, true, true, true,
				of_property_read_bool(node, "apple,always-on"),
				of_property_read_bool(node, "apple,firmware-owned"),
				res.start, resource_size(&res), offset);
	of_node_put(parent);
	return match;
}
#endif

struct apple_pmgr_ps {
	struct device *dev;
	struct generic_pm_domain genpd;
	struct reset_controller_dev rcdev;
	struct regmap *regmap;
	u32 offset;
	u32 min_state;
	bool force_disable;
	bool force_reset;
	bool externally_clocked;
	bool system_sleep;
	bool firmware_owned;
	/* Never enable automatic power management (apple,no-auto-pm). */
	bool no_auto_pm;
};

#define genpd_to_apple_pmgr_ps(_genpd) container_of(_genpd, struct apple_pmgr_ps, genpd)
#define rcdev_to_apple_pmgr_ps(_rcdev) container_of(_rcdev, struct apple_pmgr_ps, rcdev)

static int apple_pmgr_ps_set(struct generic_pm_domain *genpd, u32 pstate, bool auto_enable)
{
	int ret;
	struct apple_pmgr_ps *ps = genpd_to_apple_pmgr_ps(genpd);
	u32 reg, cur;

	ret = regmap_read(ps->regmap, ps->offset, &reg);
	if (ret < 0)
		return ret;

	/* Resets are synchronous, and only work if the device is powered and clocked. */
	if (reg & APPLE_PMGR_RESET && pstate != APPLE_PMGR_PS_ACTIVE)
		dev_err_ratelimited(ps->dev, "PS %s: powering off with RESET active\n",
				    genpd->name);

	if (pstate != APPLE_PMGR_PS_ACTIVE && (ps->force_disable || ps->force_reset)) {
		u32 reg_pre = reg & ~(APPLE_PMGR_AUTO_ENABLE | APPLE_PMGR_FLAGS |
				      APPLE_PMGR_BUSY);

		if (ps->force_disable)
			reg_pre |= APPLE_PMGR_DEV_DISABLE;
		if (ps->force_reset)
			reg_pre |= APPLE_PMGR_PS_RESET;

		ret = regmap_write(ps->regmap, ps->offset, reg_pre);
		if (ret < 0)
			return ret;

		ret = regmap_read_poll_timeout_atomic(
			ps->regmap, ps->offset, cur,
			(cur & (APPLE_PMGR_DEV_DISABLE | APPLE_PMGR_PS_RESET)) ==
			(reg_pre & (APPLE_PMGR_DEV_DISABLE | APPLE_PMGR_PS_RESET)), 1,
			APPLE_PMGR_PS_SET_TIMEOUT);

		if (ret < 0) {
			dev_err_ratelimited(ps->dev,
					    "PS %s: Failed to set reset/disable bits (now: 0x%x)\n",
					    genpd->name, cur);
			return ret;
		}
	}

	/* Do not replay the hardware-owned BUSY bit in this whole-word write. */
	reg &= ~(APPLE_PMGR_DEV_DISABLE | APPLE_PMGR_PS_RESET |
		 APPLE_PMGR_AUTO_ENABLE | APPLE_PMGR_FLAGS | APPLE_PMGR_BUSY |
		 APPLE_PMGR_PS_TARGET);
	reg |= FIELD_PREP(APPLE_PMGR_PS_TARGET, pstate);

	dev_dbg(ps->dev, "PS %s: pwrstate = 0x%x: 0x%x\n", genpd->name, pstate, reg);

	ret = regmap_write(ps->regmap, ps->offset, reg);
	if (ret < 0)
		return ret;

	if (ps->externally_clocked && pstate == APPLE_PMGR_PS_ACTIVE) {
		/*
		 * If this clock domain requires an external clock, then
		 * consider the "clock gated" state to be good enough.
		 */
		ret = regmap_read_poll_timeout_atomic(
			ps->regmap, ps->offset, cur,
			FIELD_GET(APPLE_PMGR_PS_ACTUAL, cur) >= APPLE_PMGR_PS_CLKGATE, 1,
			APPLE_PMGR_PS_SET_TIMEOUT);
	} else {
		ret = regmap_read_poll_timeout_atomic(
			ps->regmap, ps->offset, cur,
			FIELD_GET(APPLE_PMGR_PS_ACTUAL, cur) == pstate, 1,
			APPLE_PMGR_PS_SET_TIMEOUT);
	}

	if (ret < 0)
		dev_err_ratelimited(ps->dev,
				    "PS %s: Failed to reach power state 0x%x (requested 0x%x, now: 0x%x)\n",
				    genpd->name, pstate, reg, cur);

	if (!ret && auto_enable) {
		/* Not all devices implement this; this is a no-op where not implemented. */
		reg |= APPLE_PMGR_AUTO_ENABLE;
		ret = regmap_write(ps->regmap, ps->offset, reg);
	}

	return ret;
}

static int apple_pmgr_ps_is_active(struct apple_pmgr_ps *ps, bool *active)
{
	u32 reg;
	int ret;

	ret = regmap_read(ps->regmap, ps->offset, &reg);
	if (ret)
		return ret;
	/*
	 * We consider domains as active if they are actually on, or if they have auto-PM
	 * enabled and the intended target is on.
	 */
	*active = (FIELD_GET(APPLE_PMGR_PS_ACTUAL, reg) == APPLE_PMGR_PS_ACTIVE ||
		   (FIELD_GET(APPLE_PMGR_PS_TARGET, reg) == APPLE_PMGR_PS_ACTIVE &&
		    reg & APPLE_PMGR_AUTO_ENABLE));
	return 0;
}

/* Propagate read or write errors before initializing the generic domain. */
static int apple_pmgr_ps_program_min(struct apple_pmgr_ps *ps)
{
	return regmap_update_bits(ps->regmap, ps->offset,
				  APPLE_PMGR_FLAGS | APPLE_PMGR_PS_MIN,
				  FIELD_PREP(APPLE_PMGR_PS_MIN, ps->min_state));
}

static int apple_pmgr_ps_program_auto(struct apple_pmgr_ps *ps)
{
	return regmap_update_bits(ps->regmap, ps->offset,
				  APPLE_PMGR_FLAGS | APPLE_PMGR_AUTO_ENABLE,
				  ps->no_auto_pm ? 0 : APPLE_PMGR_AUTO_ENABLE);
}

#if IS_ENABLED(CONFIG_APPLE_T6034_GPU_DIAG_NO_AUTO)
/*
 * Experimental AFR boot hold. Accept an already active, ungated domain
 * without writing it, or request active from the observed on-target/auto
 * state. Restore the original auto bit if the request fails. A successful
 * synthetic test does not qualify this on real hardware.
 */
static int t6034_gpu_diag_hold_afr(struct apple_pmgr_ps *ps)
{
	u32 before, after;
	int ret, restore;

	ret = regmap_read(ps->regmap, ps->offset, &before);
	if (ret < 0)
		return ret;
	dev_info(ps->dev, "experimental AFR initial actual=%u target=%u auto=%u\n",
		 (unsigned int)FIELD_GET(APPLE_PMGR_PS_ACTUAL, before),
		 (unsigned int)FIELD_GET(APPLE_PMGR_PS_TARGET, before),
		 !!(before & APPLE_PMGR_AUTO_ENABLE));
	if (FIELD_GET(APPLE_PMGR_PS_TARGET, before) != APPLE_PMGR_PS_ACTIVE)
		return -EINVAL;
	if (!(before & APPLE_PMGR_AUTO_ENABLE))
		return FIELD_GET(APPLE_PMGR_PS_ACTUAL, before) == APPLE_PMGR_PS_ACTIVE ? 0 : -EINVAL;

	ret = apple_pmgr_ps_set(&ps->genpd, APPLE_PMGR_PS_ACTIVE, false);
	if (!ret) {
		ret = regmap_read(ps->regmap, ps->offset, &after);
		if (!ret && ((after & APPLE_PMGR_AUTO_ENABLE) ||
		    FIELD_GET(APPLE_PMGR_PS_ACTUAL, after) != APPLE_PMGR_PS_ACTIVE ||
		    FIELD_GET(APPLE_PMGR_PS_TARGET, after) != APPLE_PMGR_PS_ACTIVE))
			ret = -EIO;
	}
	if (!ret)
		return 0;

	restore = regmap_update_bits(ps->regmap, ps->offset,
				     APPLE_PMGR_FLAGS | APPLE_PMGR_AUTO_ENABLE,
				     before & APPLE_PMGR_AUTO_ENABLE);
	if (restore < 0) {
		dev_err(ps->dev, "experimental AFR hold: auto-PM restore failed: %d\n", restore);
		return restore;
	}
	return ret;
}
#endif

/*
 * genpd keeps a domain off when ->power_on() fails and on when ->power_off()
 * fails. Follow a failed transition with the opposite one, so the domain is
 * not left requesting a state genpd does not know it is in.
 *
 * The exception is a failed power-on in the noirq and syscore phases of a
 * system sleep transition: genpd ignores the error there and records the
 * domain as on. The PM core runs those phases after the suspend_late callback
 * of every device and before any resume_early callback, so keep requesting
 * the domain on between this device's two callbacks.
 */
static int apple_pmgr_ps_power_on(struct generic_pm_domain *genpd)
{
	struct apple_pmgr_ps *ps = genpd_to_apple_pmgr_ps(genpd);
	int ret;

	/* Its coprocessor's firmware owns the state; Linux only holds a vote. */
	if (ps->firmware_owned)
		return 0;

	ret = apple_pmgr_ps_set(genpd, APPLE_PMGR_PS_ACTIVE, !ps->no_auto_pm);
	if (ret && !ps->system_sleep)
		apple_pmgr_ps_set(genpd, APPLE_PMGR_PS_PWRGATE, false);

	return ret;
}

static int apple_pmgr_ps_power_off(struct generic_pm_domain *genpd)
{
	struct apple_pmgr_ps *ps = genpd_to_apple_pmgr_ps(genpd);
	int ret;

	if (ps->firmware_owned)
		return 0;

	ret = apple_pmgr_ps_set(genpd, APPLE_PMGR_PS_PWRGATE, false);
	if (ret)
		apple_pmgr_ps_set(genpd, APPLE_PMGR_PS_ACTIVE, !ps->no_auto_pm);

	return ret;
}

static void apple_pmgr_ps_set_system_sleep(struct device *dev, bool sleep)
{
	struct apple_pmgr_ps *ps = dev_get_drvdata(dev);
	unsigned long flags;

	spin_lock_irqsave(&ps->genpd.slock, flags);
	ps->system_sleep = sleep;
	spin_unlock_irqrestore(&ps->genpd.slock, flags);
}

static int apple_pmgr_ps_suspend_late(struct device *dev)
{
	apple_pmgr_ps_set_system_sleep(dev, true);

	return 0;
}

static int apple_pmgr_ps_resume_early(struct device *dev)
{
	apple_pmgr_ps_set_system_sleep(dev, false);

	return 0;
}

static int apple_pmgr_reset_assert(struct reset_controller_dev *rcdev, unsigned long id)
{
	struct apple_pmgr_ps *ps = rcdev_to_apple_pmgr_ps(rcdev);
	unsigned long flags;

	if (ps->firmware_owned)
		return -EPERM;

	spin_lock_irqsave(&ps->genpd.slock, flags);

	if (ps->genpd.status == GENPD_STATE_OFF)
		dev_err_ratelimited(ps->dev,
				    "PS 0x%x: asserting RESET while powered down\n",
				    ps->offset);

	dev_dbg(ps->dev, "PS 0x%x: assert reset\n", ps->offset);
	/* Quiesce device before asserting reset */
	regmap_update_bits(ps->regmap, ps->offset, APPLE_PMGR_FLAGS | APPLE_PMGR_DEV_DISABLE,
			   APPLE_PMGR_DEV_DISABLE);
	regmap_update_bits(ps->regmap, ps->offset, APPLE_PMGR_FLAGS | APPLE_PMGR_RESET,
			   APPLE_PMGR_RESET);

	spin_unlock_irqrestore(&ps->genpd.slock, flags);

	return 0;
}

static int apple_pmgr_reset_deassert(struct reset_controller_dev *rcdev, unsigned long id)
{
	struct apple_pmgr_ps *ps = rcdev_to_apple_pmgr_ps(rcdev);
	unsigned long flags;

	if (ps->firmware_owned)
		return -EPERM;

	spin_lock_irqsave(&ps->genpd.slock, flags);

	dev_dbg(ps->dev, "PS 0x%x: deassert reset\n", ps->offset);
	regmap_update_bits(ps->regmap, ps->offset, APPLE_PMGR_FLAGS | APPLE_PMGR_RESET, 0);
	regmap_update_bits(ps->regmap, ps->offset, APPLE_PMGR_FLAGS | APPLE_PMGR_DEV_DISABLE, 0);

	if (ps->genpd.status == GENPD_STATE_OFF)
		dev_err_ratelimited(ps->dev,
				    "PS 0x%x: RESET was deasserted while powered down\n",
				    ps->offset);

	spin_unlock_irqrestore(&ps->genpd.slock, flags);

	return 0;
}

static int apple_pmgr_reset_reset(struct reset_controller_dev *rcdev, unsigned long id)
{
	int ret;

	ret = apple_pmgr_reset_assert(rcdev, id);
	if (ret)
		return ret;

	usleep_range(APPLE_PMGR_RESET_TIME, 2 * APPLE_PMGR_RESET_TIME);

	return apple_pmgr_reset_deassert(rcdev, id);
}

static int apple_pmgr_reset_status(struct reset_controller_dev *rcdev, unsigned long id)
{
	struct apple_pmgr_ps *ps = rcdev_to_apple_pmgr_ps(rcdev);
	u32 reg = 0;

	regmap_read(ps->regmap, ps->offset, &reg);

	return !!(reg & APPLE_PMGR_RESET);
}

static const struct reset_control_ops apple_pmgr_reset_ops = {
	.assert		= apple_pmgr_reset_assert,
	.deassert	= apple_pmgr_reset_deassert,
	.reset		= apple_pmgr_reset_reset,
	.status		= apple_pmgr_reset_status,
};

static int apple_pmgr_reset_xlate(struct reset_controller_dev *rcdev,
				  const struct of_phandle_args *reset_spec)
{
	return 0;
}

static int apple_pmgr_ps_probe(struct platform_device *pdev)
{
	struct device *dev = &pdev->dev;
	struct device_node *node = dev->of_node;
	struct apple_pmgr_ps *ps;
	struct regmap *regmap;
	struct of_phandle_iterator it;
	int ret;
	const char *name;
	bool active;
#if IS_ENABLED(CONFIG_APPLE_T6034_GPU_DIAG_NO_AUTO)
	bool diag_afr;
#endif

	regmap = syscon_node_to_regmap(node->parent);
	if (IS_ERR(regmap))
		return PTR_ERR(regmap);

	ps = devm_kzalloc(dev, sizeof(*ps), GFP_KERNEL);
	if (!ps)
		return -ENOMEM;

	ps->dev = dev;
	ps->regmap = regmap;
	platform_set_drvdata(pdev, ps);

	ret = of_property_read_string(node, "label", &name);
	if (ret < 0) {
		dev_err(dev, "missing label property\n");
		return ret;
	}

	ret = of_property_read_u32(node, "reg", &ps->offset);
	if (ret < 0) {
		dev_err(dev, "missing reg property\n");
		return ret;
	}

	ps->genpd.flags |= GENPD_FLAG_IRQ_SAFE;
	ps->genpd.name = name;
	ps->genpd.power_on = apple_pmgr_ps_power_on;
	ps->genpd.power_off = apple_pmgr_ps_power_off;

	/*
	 * A domain whose coprocessor the boot loader hands over running (the DCP) is
	 * owned by that coprocessor's firmware: writing its state faults. Only read
	 * it, keep it on and never change its target or automatic gating.
	 */
	ps->firmware_owned = of_property_read_bool(node, "apple,firmware-owned");

	ret = of_property_read_u32(node, "apple,min-state", &ps->min_state);
	if (ret == 0 && ps->min_state <= APPLE_PMGR_PS_ACTIVE && !ps->firmware_owned) {
		ret = apple_pmgr_ps_program_min(ps);
		if (ret) {
			dev_err(dev, "cannot set minimum power state: %d\n", ret);
			return ret;
		}
	}

	if (of_property_read_bool(node, "apple,force-disable"))
		ps->force_disable = true;

	if (of_property_read_bool(node, "apple,force-reset"))
		ps->force_reset = true;

	if (of_property_read_bool(node, "apple,externally-clocked"))
		ps->externally_clocked = true;

	/*
	 * A coprocessor CPU domain whose firmware Linux starts cold must not be
	 * gated automatically behind its back: keep it at the requested state.
	 */
	ps->no_auto_pm = of_property_read_bool(node, "apple,no-auto-pm");
#if IS_ENABLED(CONFIG_APPLE_T6034_GPU_DIAG_NO_AUTO)
	if (t6034_gpu_diag_domain(node, ps->offset)) {
		ps->no_auto_pm = true;
		dev_warn(dev, "experimental J514M GPU diagnostic: automatic gating disabled\n");
	}
	diag_afr = t6034_gpu_diag_afr_domain(node, ps->offset);
#endif

	ret = apple_pmgr_ps_is_active(ps, &active);
	if (ret) {
		dev_err(dev, "cannot read initial power state: %d\n", ret);
		return ret;
	}
#if IS_ENABLED(CONFIG_APPLE_T6034_GPU_DIAG_NO_AUTO)
	if (diag_afr) {
		ret = t6034_gpu_diag_hold_afr(ps);
		if (ret)
			return ret;
		active = true;
		ps->no_auto_pm = true;
		dev_warn(dev, "experimental J514M AFR hold: shared domain kept active\n");
	}
#endif
	if (ps->firmware_owned) {
		/* Consumers (the boot framebuffer) need it; never try to turn it on. */
		if (!active)
			dev_warn(dev, "firmware-owned domain %s does not read as on\n", name);
		ps->genpd.flags |= GENPD_FLAG_ALWAYS_ON;
		active = true;
	} else if (of_property_read_bool(node, "apple,always-on")) {
		ps->genpd.flags |= GENPD_FLAG_ALWAYS_ON;
		if (!active) {
			/*
			 * The boot loader need not leave an always-on domain on,
			 * so this is not an error: apple_pmgr_ps_set() reports a
			 * domain that cannot be turned on.
			 */
			dev_info(dev, "always-on domain %s is off at boot, turning it on\n",
				 name);
			/*
			 * Turn it on so pm_genpd_init does not fail. genpd never
			 * powers an always-on domain, so keep requesting it on
			 * even if it does not get there in time.
			 */
			active = apple_pmgr_ps_set(&ps->genpd, APPLE_PMGR_PS_ACTIVE,
						   !ps->no_auto_pm) == 0;
		}
	} else if (active) {
		ps->genpd.flags |= GENPD_FLAG_DEFER_OFF | GENPD_FLAG_ACTIVE_WAKEUP;
	}

	/* Turn on auto-PM if the domain is already on, unless it must stay off */
	if (active && !ps->firmware_owned) {
		ret = apple_pmgr_ps_program_auto(ps);
		if (ret) {
			dev_err(dev, "cannot configure automatic power management: %d\n", ret);
			return ret;
		}
	}

	ret = pm_genpd_init(&ps->genpd, NULL, !active);
	if (ret < 0) {
		dev_err(dev, "pm_genpd_init failed\n");
		return ret;
	}

	ret = of_genpd_add_provider_simple(node, &ps->genpd);
	if (ret < 0) {
		dev_err(dev, "of_genpd_add_provider_simple failed\n");
		return ret;
	}

	of_for_each_phandle(&it, ret, node, "power-domains", "#power-domain-cells", -1) {
		struct of_phandle_args parent, child;

		parent.np = it.node;
		parent.args_count = of_phandle_iterator_args(&it, parent.args, MAX_PHANDLE_ARGS);
		child.np = node;
		child.args_count = 0;
		ret = of_genpd_add_subdomain(&parent, &child);

		if (ret == -EPROBE_DEFER) {
			of_node_put(parent.np);
			goto err_remove;
		} else if (ret < 0) {
			dev_err(dev, "failed to add to parent domain: %d (%s -> %s)\n",
				ret, it.node->name, node->name);
			of_node_put(parent.np);
			goto err_remove;
		}
	}

	/*
	 * Do not participate in regular PM; parent power domains are handled via the
	 * genpd hierarchy.
	 */
	pm_genpd_remove_device(dev);

	ps->rcdev.owner = THIS_MODULE;
	ps->rcdev.nr_resets = 1;
	ps->rcdev.ops = &apple_pmgr_reset_ops;
	ps->rcdev.of_node = dev->of_node;
	ps->rcdev.of_reset_n_cells = 0;
	ps->rcdev.of_xlate = apple_pmgr_reset_xlate;

	ret = devm_reset_controller_register(dev, &ps->rcdev);
	if (ret < 0)
		goto err_remove;

	return 0;
err_remove:
	of_genpd_del_provider(node);
	pm_genpd_remove(&ps->genpd);
	return ret;
}

static const struct of_device_id apple_pmgr_ps_of_match[] = {
	{ .compatible = "apple,t8103-pmgr-pwrstate" },
	{ .compatible = "apple,pmgr-pwrstate" },
	{}
};

MODULE_DEVICE_TABLE(of, apple_pmgr_ps_of_match);

static const struct dev_pm_ops apple_pmgr_ps_pm_ops = {
	LATE_SYSTEM_SLEEP_PM_OPS(apple_pmgr_ps_suspend_late, apple_pmgr_ps_resume_early)
};

static struct platform_driver apple_pmgr_ps_driver = {
	.probe = apple_pmgr_ps_probe,
	.driver = {
		.name = "apple-pmgr-pwrstate",
		.of_match_table = apple_pmgr_ps_of_match,
		.pm = pm_sleep_ptr(&apple_pmgr_ps_pm_ops),
	},
};

MODULE_AUTHOR("Hector Martin <marcan@marcan.st>");
MODULE_DESCRIPTION("PMGR power state driver for Apple SoCs");

module_platform_driver(apple_pmgr_ps_driver);

#if IS_ENABLED(CONFIG_APPLE_PMGR_PWRSTATE_KUNIT_TEST)
#include "pmgr-pwrstate-test.c"
#endif
